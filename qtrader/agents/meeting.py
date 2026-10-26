"""AI 투자 회의 진행. 각 참석자의 상태를 실시간으로 남겨 픽셀 사무실 화면이 따라 그린다.

흐름 (TradingAgents + 타민더마켓 리서치 절차)
  1 분석팀 5명 동시 작성 (기술 / 재무·밸류 / 실적·가이던스 / 뉴스 / 수급·심리)
  2 검증관: 핵심 수치 팩트체크 + 추가 질문 5가지 조사 + 밸류에이션·점수 정리 (JSON)
  3 강세·약세 토론 (라운드 설정)
  4 리서치 팀장: 투자 논문, 숏 논거와 반박, 시나리오, 등급
  5 트레이더 실행안
  6 리스크 위원회 3명 토론
  7 대표: 최종 결정 (JSON)
"""
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
from .roles import BY_ID, CHECK_SCHEMA, FINAL_SCHEMA, RATING_KO, ROLES, STAGES, headline, system_for

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
        self.quick = ai.get("quick_model") or ai.get("model")
        self.deep = ai.get("deep_model") or ai.get("model")
        self.llm = llm or LLM(self.deep, ai["effort"])
        self.debate_rounds = int(ai.get("debate_rounds", 1))
        self.risk_rounds = int(ai.get("risk_rounds", 1))
        self.web = bool(ai.get("web_research", True))
        self.profile = ai.get("profile", "")
        market = ctxmod.market_of(symbol)
        self.run = {
            "id": uuid.uuid4().hex[:12], "symbol": symbol, "market": market.value, "name": symbol,
            "trade_date": trade_date(market), "started_at": now(), "finished_at": None,
            "status": "running", "stage": "자료 준비", "stage_key": "prep", "final": None, "check": None,
            "facts": None, "error": None, "tokens_in": 0, "tokens_out": 0,
            "stages": {k: "idle" for k, _ in STAGES},
            "agents": {r.id: {"status": "idle", "bubble": "", "report": "", "sources": []} for r in ROLES},
            "debate": [], "risk_debate": [], "handoffs": [],
        }
        with _lock:
            _live[self.run["id"]] = self.run

    # 상태 갱신
    def _set(self, rid: str, **kw) -> None:
        with _lock:
            self.run["agents"][rid].update(kw)

    def _stage(self, key: str) -> None:
        with _lock:
            for k, _ in STAGES:
                if self.run["stages"][k] == "working":
                    self.run["stages"][k] = "done"
            if key in self.run["stages"]:
                self.run["stages"][key] = "working"
            self.run["stage_key"] = key
            self.run["stage"] = dict(STAGES).get(key, key)

    def _handoff(self, src: str, dst: str) -> None:
        """화면에서 서류가 날아가는 연출용 기록."""
        with _lock:
            self.run["handoffs"].append({"from": src, "to": dst, "at": now()})

    def _say(self, rid: str, user: str, web: bool = False, schema: dict | None = None, working: str = "") -> str:
        role = BY_ID[rid]
        self._set(rid, status="working", bubble=working or "생각 중...")
        rep = self.llm.run(system_for(role, self.profile), user, web=web and self.web, schema=schema,
                           model=self.deep if role.deep else self.quick)
        with _lock:
            self.run["tokens_in"] += rep.input_tokens
            self.run["tokens_out"] += rep.output_tokens
        text = rep.text
        if schema is FINAL_SCHEMA:
            self.run["final"] = rep.data
            bubble = f"{RATING_KO.get(rep.data['rating'], rep.data['rating'])} · {rep.data['headline']}"
        elif schema is CHECK_SCHEMA:
            self.run["check"] = rep.data
            fc = rep.data.get("fact_checks", [])
            ok = sum(1 for x in fc if x.get("status") == "확인됨")
            bubble = f"수치 {len(fc)}건 중 {ok}건 확인, 질문 {len(rep.data.get('key_questions', []))}개 조사"
            text = rep.data.get("summary", "") + "\n\n" + "\n".join(
                f"- [{x['status']}] {x['claim']}: {x['detail']} ({x['source']})" for x in fc) + "\n\n" + "\n".join(
                f"Q. {q['question']}\nA. {q['answer']} ({q['source']})" for q in rep.data.get("key_questions", []))
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
                with _lock:
                    for k in self.run["stages"]:
                        if self.run["stages"][k] == "working":
                            self.run["stages"][k] = "done" if self.run["status"] == "done" else "error"
                    self.run["stage"] = "완료" if self.run["status"] == "done" else "오류"
                self.store.save_ai_run(self.run)
        return self.run

    def _execute(self) -> None:
        market = ctxmod.market_of(self.symbol)
        name, flows, fx = self.symbol, None, None
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
            else:
                try:
                    fx = float(self.toss.exchange_rate().get("rate") or 0) or None
                except Exception:
                    fx = None
        self.run["name"] = name
        bars = self.data.bars(market, self.symbol, 260)
        brief = ctxmod.build(self.symbol, bars, name, flows)
        self.run["facts"] = brief.facts | {"levels": brief.levels}
        facts = brief.text()
        today = datetime.now().strftime("%Y-%m-%d")
        fx_line = f"\n원/달러 환율: {fx:,.1f}원" if fx else ""
        base = f"오늘은 {today}이다.\n\n[회의 자료]\n{facts}{fx_line}"

        # 1) 분석팀 5명 동시 진행
        self._stage("analysts")
        asks = {
            "chart": f"{base}\n\n기술적 분석 보고서를 써라.",
            "fund": f"{base}\n\n{name}의 재무·사업·밸류에이션 보고서를 써라. 최신 공시와 IR 자료를 웹에서 찾아라.",
            "earn": f"{base}\n\n{name}의 최근 실적 발표와 가이던스 변화 보고서를 써라. 웹에서 찾아라.",
            "news": f"{base}\n\n{name} 관련 최근 뉴스와 예정 이벤트 보고서를 써라. 웹에서 찾아라.",
            "flow": f"{base}\n\n{name}의 수급과 투자자 심리 보고서를 써라. 여론과 기관·내부자 매매는 웹에서 찾아라.",
        }
        with ThreadPoolExecutor(5) as pool:
            futs = {rid: pool.submit(self._say, rid, q, BY_ID[rid].web, None, "자료 검토 중...")
                    for rid, q in asks.items()}
            reports = {rid: f.result() for rid, f in futs.items()}
        for rid in asks:
            self._handoff(rid, "checker")
        packet = base + "\n\n" + "\n\n".join(f"[{BY_ID[r].title} 보고서]\n{t}" for r, t in reports.items())

        # 2) 검증관: 팩트체크 + 추가 질문 5가지 + 밸류에이션·점수
        self._stage("check")
        checked = self._say("checker", f"{packet}\n\n위 보고서의 핵심 수치·주장 6~10개를 원자료로 다시 확인하고, "
                                       "투자 전에 꼭 알아야 할 추가 질문 5가지를 조사해 답하라. "
                                       "밸류에이션 지표(현재/5년 평균/경쟁사 평균)와 분야별 점수(0~100)도 채워라.",
                            web=True, schema=CHECK_SCHEMA, working="원자료 대조 중...")
        self._handoff("checker", "bull")
        self._handoff("checker", "bear")
        packet += f"\n\n[검증관 팩트체크와 추가 질문]\n{checked}"

        # 3) 강세/약세 토론
        self._stage("debate")
        history = ""
        for i in range(max(1, self.debate_rounds)):
            for rid in ("bull", "bear"):
                t = self._say(rid, f"{packet}\n\n[지금까지의 토론]\n{history or '(첫 발언)'}\n\n"
                                   f"{i + 1}라운드 발언을 해라. 상대 주장에 반박하라.", working="반론 준비 중...")
                history += f"\n\n[{BY_ID[rid].title} {i + 1}라운드]\n{t}"
                self.run["debate"].append({"role": rid, "round": i + 1, "text": t})
        self._handoff("bull", "lead")
        self._handoff("bear", "lead")

        # 4) 리서치 팀장
        self._stage("lead")
        plan = self._say("lead", f"{packet}\n\n[토론]\n{history}\n\n투자 계획과 투자 논문을 써라.", working="토론 정리 중...")
        self._handoff("lead", "trader")

        # 5) 트레이더
        self._stage("trader")
        trade = self._say("trader", f"{base}\n\n[투자 계획]\n{plan}\n\n실행안을 써라.", working="주문 설계 중...")
        for rid in ("aggr", "neut", "cons"):
            self._handoff("trader", rid)

        # 6) 리스크 위원회
        self._stage("risk")
        rhist = ""
        for i in range(max(1, self.risk_rounds)):
            for rid in ("aggr", "neut", "cons"):
                t = self._say(rid, f"{base}\n\n[투자 계획]\n{plan}\n\n[트레이더 실행안]\n{trade}\n\n"
                                   f"[리스크 토론]\n{rhist or '(첫 발언)'}\n\n{i + 1}라운드 의견을 내라. "
                                   "마지막 줄에 '의견: 확대/유지/축소/관망' 중 하나를 써라.", working="위험 점검 중...")
                rhist += f"\n\n[{BY_ID[rid].title} {i + 1}라운드]\n{t}"
                self.run["risk_debate"].append({"role": rid, "round": i + 1, "text": t})
        for rid in ("aggr", "neut", "cons"):
            self._handoff(rid, "pm")

        # 7) 대표 최종 결정
        self._stage("pm")
        self._say("pm", f"{packet}\n\n[토론]\n{history}\n\n[투자 계획]\n{plan}\n\n[실행안]\n{trade}\n\n[리스크 토론]\n{rhist}\n\n"
                        "최종 결정을 JSON 으로 내라. confidence·probability 는 0~1, 손절·익절은 % 양수, "
                        "시나리오 가격과 진입 가격대는 회의 자료 통화 기준. summary·reasons·trust·worry·watch 는 각 3개, "
                        "short_args 는 3개. risk_votes 는 리스크 위원 3명의 마지막 의견.",
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
