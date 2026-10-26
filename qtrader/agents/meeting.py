"""AI 투자 회의 진행. 각 참석자의 상태를 실시간으로 남겨 픽셀 사무실 화면이 따라 그린다."""
from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..engine import trade_date
from ..storage import Store, now
from . import context as ctxmod
from .llm import LLM, LLMError
from .roles import BY_ID, FINAL_SCHEMA, RATING_KO, ROLES, headline, system_for

_lock = threading.Lock()
_live: dict[str, dict] = {}  # 진행 중·최근 회의 (메모리)
_gate = threading.Semaphore(1)  # 비용·호출 한도 때문에 회의는 한 번에 하나씩


def live_runs() -> list[dict]:
    with _lock:
        return [copy.deepcopy(r) for r in sorted(_live.values(), key=lambda r: r["started_at"], reverse=True)]


def get_run(run_id: str) -> dict | None:
    with _lock:
        r = _live.get(run_id)
        return copy.deepcopy(r) if r else None


def running_for(symbol: str) -> str | None:
    with _lock:
        for r in _live.values():
            if r["symbol"] == symbol and r["status"] == "running":
                return r["id"]
    return None


class Meeting:
    def __init__(self, symbol: str, data, store: Store, cfg: dict, llm: LLM | None = None, toss=None):
        self.symbol, self.data, self.store, self.cfg, self.toss = symbol, data, store, cfg, toss
        ai = cfg["ai"]
        self.llm = llm or LLM(ai["model"], ai["effort"])
        self.debate_rounds = int(ai.get("debate_rounds", 1))
        self.risk_rounds = int(ai.get("risk_rounds", 1))
        self.web = bool(ai.get("web_research", True))
        market = ctxmod.market_of(symbol)
        self.run = {
            "id": uuid.uuid4().hex[:12], "symbol": symbol, "market": market.value, "name": symbol,
            "trade_date": trade_date(market), "started_at": now(), "finished_at": None,
            "status": "running", "stage": "자료 준비", "final": None, "error": None,
            "tokens_in": 0, "tokens_out": 0,
            "agents": {r.id: {"status": "idle", "bubble": "", "report": "", "sources": []} for r in ROLES},
            "debate": [], "risk_debate": [],
        }
        with _lock:
            _live[self.run["id"]] = self.run

    # 상태 갱신
    def _set(self, rid: str, **kw) -> None:
        with _lock:
            self.run["agents"][rid].update(kw)

    def _stage(self, s: str) -> None:
        with _lock:
            self.run["stage"] = s

    def _say(self, rid: str, user: str, web: bool = False, schema: dict | None = None, working: str = "") -> str:
        role = BY_ID[rid]
        self._set(rid, status="working", bubble=working or "생각 중...")
        rep = self.llm.run(system_for(role), user, web=web and self.web, schema=schema)
        with _lock:
            self.run["tokens_in"] += rep.input_tokens
            self.run["tokens_out"] += rep.output_tokens
        text = rep.text
        if schema:
            self.run["final"] = rep.data
            bubble = f"{RATING_KO.get(rep.data['rating'], rep.data['rating'])} · {rep.data['headline']}"
        else:
            bubble = headline(text)
        prev = self.run["agents"][rid]["report"]
        self._set(rid, status="done", bubble=bubble, report=(prev + "\n\n---\n\n" + text) if prev else text,
                  sources=self.run["agents"][rid]["sources"] + rep.sources)
        return text

    def execute(self) -> dict:
        with _gate:
            try:
                self._execute()
                self.run["status"] = "done"
            except Exception as e:  # LLMError, 데이터 오류 모두 화면에 보여준다
                self.run["status"] = "error"
                self.run["error"] = str(e)
                for a in self.run["agents"].values():
                    if a["status"] == "working":
                        a.update(status="error", bubble="중단됨")
            finally:
                self.run["finished_at"] = now()
                self._stage("완료" if self.run["status"] == "done" else "오류")
                self.store.save_ai_run(self.run)
        return self.run

    def _execute(self) -> None:
        market = ctxmod.market_of(self.symbol)
        name, flows = self.symbol, None
        if self.toss is not None:
            try:
                info = self.toss.stocks([self.symbol])
                if info:
                    name = info[0].get("name") or name
            except Exception:
                pass
            if market.value == "KR":
                try:
                    flows = self.toss.stock_investor_trading(self.symbol, 5)
                except Exception:
                    flows = None
        self.run["name"] = name
        bars = self.data.bars(market, self.symbol, 260)
        brief = ctxmod.build(self.symbol, bars, name, flows)
        facts = brief.text()
        today = datetime.now().strftime("%Y-%m-%d")
        base = f"오늘은 {today}이다.\n\n[회의 자료]\n{facts}"

        # 1) 분석가 4명 동시 진행
        self._stage("분석팀 보고서 작성")
        asks = {
            "chart": f"{base}\n\n기술적 분석 보고서를 써라.",
            "fund": f"{base}\n\n{name}의 재무·밸류에이션 보고서를 써라. 최신 자료를 웹에서 찾아라.",
            "news": f"{base}\n\n{name} 관련 최근 뉴스 보고서를 써라. 웹에서 찾아라.",
            "flow": f"{base}\n\n{name}의 수급과 투자자 심리 보고서를 써라. 여론은 웹에서 찾아라.",
        }
        with ThreadPoolExecutor(4) as pool:
            futs = {rid: pool.submit(self._say, rid, q, BY_ID[rid].web, None, "자료 검토 중...")
                    for rid, q in asks.items()}
            reports = {rid: f.result() for rid, f in futs.items()}
        packet = base + "\n\n" + "\n\n".join(f"[{BY_ID[r].title} 보고서]\n{t}" for r, t in reports.items())

        # 2) 강세/약세 토론
        self._stage("강세·약세 토론")
        history = ""
        for i in range(max(1, self.debate_rounds)):
            for rid in ("bull", "bear"):
                t = self._say(rid, f"{packet}\n\n[지금까지의 토론]\n{history or '(첫 발언)'}\n\n"
                                   f"{i + 1}라운드 발언을 해라. 상대 주장에 반박하라.", working="반론 준비 중...")
                history += f"\n\n[{BY_ID[rid].title} {i + 1}라운드]\n{t}"
                self.run["debate"].append({"role": rid, "round": i + 1, "text": t})

        # 3) 리서치 팀장 -> 투자 계획
        self._stage("리서치 팀장 판정")
        plan = self._say("lead", f"{packet}\n\n[토론]\n{history}\n\n투자 계획을 써라.", working="토론 정리 중...")

        # 4) 트레이더 실행안
        self._stage("트레이더 실행안")
        trade = self._say("trader", f"{base}\n\n[투자 계획]\n{plan}\n\n실행안을 써라.", working="주문 설계 중...")

        # 5) 리스크 위원회 토론
        self._stage("리스크 위원회")
        rhist = ""
        for i in range(max(1, self.risk_rounds)):
            for rid in ("aggr", "neut", "cons"):
                t = self._say(rid, f"{base}\n\n[투자 계획]\n{plan}\n\n[트레이더 실행안]\n{trade}\n\n"
                                   f"[리스크 토론]\n{rhist or '(첫 발언)'}\n\n{i + 1}라운드 의견을 내라.",
                              working="위험 점검 중...")
                rhist += f"\n\n[{BY_ID[rid].title} {i + 1}라운드]\n{t}"
                self.run["risk_debate"].append({"role": rid, "round": i + 1, "text": t})

        # 6) 포트폴리오 매니저 최종 결정
        self._stage("대표 최종 결정")
        self._say("pm", f"{packet}\n\n[투자 계획]\n{plan}\n\n[실행안]\n{trade}\n\n[리스크 토론]\n{rhist}\n\n"
                        "최종 결정을 JSON 으로 내라. confidence 는 0~1, 손절·익절은 % 양수, reasons·risks 는 각 3개 이내.",
                  schema=FINAL_SCHEMA, working="최종 결정 중...")


def start(symbol: str, data, store: Store, cfg: dict, llm: LLM | None = None, toss=None,
          background: bool = True) -> dict:
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("종목을 입력하세요")
    existing = running_for(symbol)
    if existing:
        return get_run(existing)
    m = Meeting(symbol, data, store, cfg, llm, toss)
    if background:
        threading.Thread(target=m.execute, name=f"meeting-{symbol}", daemon=True).start()
        return get_run(m.run["id"])
    return m.execute()


__all__ = ["Meeting", "start", "live_runs", "get_run", "LLMError"]
