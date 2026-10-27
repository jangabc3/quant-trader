"""AI 투자 회의 진행. 각 참석자의 상태를 실시간으로 남겨 픽셀 사무실 화면이 따라 그린다.

두 가지 모드
- 빠른 회의 (기본, Claude 호출 7번): 분석가 3번(기술 / 재무+실적 / 뉴스+수급) -> 검증관 -> 강세·약세 동시 ->
  대표가 팀장·트레이더·리스크 위원회 역할까지 묶어 최종 결정
- 정밀 회의 (14번): 분석가 5명 -> 검증관 -> 강세·약세(1라운드 동시, 이후 차례로) -> 팀장 -> 트레이더 ->
  리스크 3명 동시 -> 대표

토큰 절약
- 모든 참석자가 같은 지침(system)과 같은 회의 자료(context)를 받고 캐시 지점을 둔다.
  같은 자료를 두 번째로 읽는 참석자부터는 입력 요금의 1/10~1/20 인 캐시 요금만 든다.
- 참석자별 토큰과 예상 비용을 기록해 화면에 보여준다.

회의가 끝나면 숫자 규칙으로 매수 체크리스트(principles.checklist)를 돌리고, 진입·손절·익절 가격과 수량(trade_plan)을 만든다.
"""
from __future__ import annotations

import copy
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .. import principles
from ..engine import trade_date
from ..storage import Store, now
from . import context as ctxmod
from .llm import LLM, LLMError
from .roles import BY_ID, CHECK_SCHEMA, FINAL_SCHEMA, RATING_KO, ROLES, STAGES, common_system, headline, role_brief

_lock = threading.Lock()
_live: dict[str, dict] = {}  # 진행 중·최근 회의 (메모리)
_gate = threading.Semaphore(1)  # 비용·호출 한도 때문에 회의는 한 번에 하나씩

# 빠른 회의에서 한 번의 호출이 여러 참석자 몫을 할 때 (화면의 캐릭터도 같이 움직인다)
FAST_MERGE = {"fund": ["earn"], "news": ["flow"], "pm": ["lead", "trader", "aggr", "neut", "cons"]}


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
        self.mode = ai.get("mode", "fast")
        self.debate_rounds = int(ai.get("debate_rounds", 1))
        self.risk_rounds = int(ai.get("risk_rounds", 1))
        self.web = bool(ai.get("web_research", True))
        self.system = common_system(ai.get("profile", ""))
        market = ctxmod.market_of(symbol)
        self.market = market
        self.run = {
            "id": uuid.uuid4().hex[:12], "symbol": symbol, "market": market.value, "name": symbol, "mode": self.mode,
            "trade_date": trade_date(market), "started_at": now(), "finished_at": None,
            "status": "running", "stage": "자료 준비", "stage_key": "prep", "final": None, "check": None,
            "facts": None, "plan": None, "checklist": None, "error": None,
            "tokens_in": 0, "tokens_out": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0, "calls": 0,
            "stages": {k: "idle" for k, _ in STAGES},
            "agents": {r.id: {"status": "idle", "bubble": "", "report": "", "sources": [], "tokens": 0, "cost": 0.0}
                       for r in ROLES},
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
        with _lock:
            self.run["handoffs"].append({"from": src, "to": dst, "at": now()})

    def _say(self, rid: str, context: str, ask: str, web: bool = False, schema: dict | None = None,
             working: str = "", also: list[str] | None = None) -> str:
        role = BY_ID[rid]
        for r in [rid, *(also or [])]:
            self._set(r, status="working", bubble=working or "생각 중...")
        rep = self.llm.run(self.system, f"{role_brief(role)}\n\n{ask}", web=web and self.web, schema=schema,
                           model=self.deep if role.deep else self.quick, context=context)
        cost = rep.cost_usd() if hasattr(rep, "cost_usd") else 0.0
        with _lock:
            self.run["tokens_in"] += rep.input_tokens
            self.run["tokens_out"] += rep.output_tokens
            self.run["cache_read"] += getattr(rep, "cache_read", 0)
            self.run["cache_write"] += getattr(rep, "cache_write", 0)
            self.run["cost_usd"] = round(self.run["cost_usd"] + cost, 4)
            self.run["calls"] += 1
        text = rep.text
        if schema is FINAL_SCHEMA:
            d = rep.data
            self.run["final"] = d
            bubble = f"{RATING_KO.get(d['rating'], d['rating'])} · {d['headline']}"
            # 보고서 칸에는 JSON 대신 읽을 수 있는 문장으로
            text = (f"한줄요약: {d['headline']}\n\n**최종 의견** {RATING_KO.get(d['rating'], d['rating'])} "
                    f"(확신도 {d['confidence']:.0%})\n\n**투자 논문** {d['thesis']}\n\n**핵심 근거**\n"
                    + "\n".join(f"- {x}" for x in d["reasons"]) + "\n\n**위험**\n"
                    + "\n".join(f"- {x}" for x in d["worry"])
                    + f"\n\n**손절** -{d['stop_loss_pct']}% · **익절** +{d['take_profit_pct']}% · **보유 기간** {d['horizon_days']}거래일")
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
        tok = rep.input_tokens + rep.output_tokens + getattr(rep, "cache_read", 0)
        a = self.run["agents"][rid]
        self._set(rid, status="done", bubble=bubble, report=(a["report"] + "\n\n---\n\n" + text) if a["report"] else text,
                  sources=a["sources"] + rep.sources, tokens=a["tokens"] + tok, cost=round(a["cost"] + cost, 4))
        for r in also or []:
            self._set(r, status="done", bubble=f"{role.name} 보고서에 함께 정리", report=text)
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
            try:
                from .. import extras
                f = self.run.get("final") or {}
                extras.notify(self.store, self.cfg, f"🤖 {self.run.get('name') or self.symbol} AI 회의 끝: "
                              + (f"{f.get('rating')} (확신 {float(f.get('confidence') or 0):.0%})"
                                 if self.run["status"] == "done" else f"오류 {self.run.get('error', '')[:80]}"), "ai")
            except Exception:
                pass
        return self.run

    def _gather(self):
        name, flows, fx, warnings = self.symbol, None, None, []
        if self.toss is not None:
            try:
                info = self.toss.stocks([self.symbol])
                if info:
                    name = info[0].get("name") or name
            except Exception:
                pass
            try:
                warnings = self.toss.warnings(self.symbol)
            except Exception:
                warnings = []
            if self.market.value == "KR":
                try:
                    flows = self.toss.stock_investor_trading(self.symbol, 5)
                except Exception:
                    flows = None
            else:
                try:
                    fx = float(self.toss.exchange_rate().get("rate") or 0) or None
                except Exception:
                    fx = None
        return name, flows, fx, warnings

    def _execute(self) -> None:
        name, flows, fx, warnings = self._gather()
        self.run["name"] = name
        bars = self.data.bars(self.market, self.symbol, 300)
        brief = ctxmod.build(self.symbol, bars, name, flows)
        self.run["facts"] = brief.facts | {"levels": brief.levels}
        today = datetime.now().strftime("%Y-%m-%d")
        fx_line = f"\n원/달러 환율: {fx:,.1f}원" if fx else ""
        warn_line = f"\n거래소 유의사항: {', '.join(w.get('warningType', '') for w in warnings)}" if warnings else ""
        base = f"오늘은 {today}이다.\n\n[회의 자료]\n{brief.text()}{fx_line}{warn_line}"
        fast = self.mode != "precise"

        # 1) 분석팀
        self._stage("analysts")
        if fast:
            asks = {
                "chart": ("기술적 분석 보고서를 써라.", None),
                "fund": (f"{name}의 재무·사업·밸류에이션과 최근 실적·가이던스 변화를 한 보고서로 써라. 공시·IR을 웹에서 찾아라.", ["earn"]),
                "news": (f"{name}의 최근 뉴스·예정 이벤트와 수급·투자자 심리를 한 보고서로 써라. 웹에서 찾아라.", ["flow"]),
            }
        else:
            asks = {
                "chart": ("기술적 분석 보고서를 써라.", None),
                "fund": (f"{name}의 재무·사업·밸류에이션 보고서를 써라. 최신 공시와 IR 자료를 웹에서 찾아라.", None),
                "earn": (f"{name}의 최근 실적 발표와 가이던스 변화 보고서를 써라. 웹에서 찾아라.", None),
                "news": (f"{name} 관련 최근 뉴스와 예정 이벤트 보고서를 써라. 웹에서 찾아라.", None),
                "flow": (f"{name}의 수급과 투자자 심리 보고서를 써라. 여론과 기관·내부자 매매는 웹에서 찾아라.", None),
            }
        with ThreadPoolExecutor(len(asks)) as pool:
            futs = {rid: pool.submit(self._say, rid, base, q, BY_ID[rid].web, None, "자료 검토 중...", also)
                    for rid, (q, also) in asks.items()}
            reports = {rid: f.result() for rid, f in futs.items()}
        for rid in ("chart", "fund", "earn", "news", "flow"):
            self._handoff(rid, "checker")
        packet = base + "\n\n" + "\n\n".join(f"[{BY_ID[r].title} 보고서]\n{t}" for r, t in reports.items())

        # 2) 검증관
        self._stage("check")
        checked = self._say("checker", packet, "위 보고서의 핵심 수치·주장 6~10개를 원자료로 다시 확인하고, 투자 전에 꼭 알아야 할 "
                            "추가 질문 5가지를 조사해 답하라. 밸류에이션 지표(현재/5년 평균/경쟁사 평균)와 분야별 점수(0~100)도 채워라.",
                            web=True, schema=CHECK_SCHEMA, working="원자료 대조 중...")
        self._handoff("checker", "bull")
        self._handoff("checker", "bear")
        packet += f"\n\n[검증관 팩트체크와 추가 질문]\n{checked}"

        # 3) 강세·약세: 1라운드는 동시에 (서로의 첫 주장을 모른 채 독립적으로)
        self._stage("debate")
        with ThreadPoolExecutor(2) as pool:
            fb = {rid: pool.submit(self._say, rid, packet, "1라운드 주장을 해라. 핵심 근거 3가지.", False, None, "논리 세우는 중...")
                  for rid in ("bull", "bear")}
            first = {rid: f.result() for rid, f in fb.items()}
        history = "".join(f"\n\n[{BY_ID[r].title} 1라운드]\n{t}" for r, t in first.items())
        self.run["debate"] += [{"role": r, "round": 1, "text": t} for r, t in first.items()]
        for i in range(2, self.debate_rounds + 1 if not fast else 1):
            for rid in ("bull", "bear"):
                t = self._say(rid, packet, f"[지금까지의 토론]{history}\n\n{i}라운드: 상대 주장에 구체적으로 반박하라.",
                              working="반론 준비 중...")
                history += f"\n\n[{BY_ID[rid].title} {i}라운드]\n{t}"
                self.run["debate"].append({"role": rid, "round": i, "text": t})
        packet += f"\n\n[강세·약세 토론]{history}"
        self._handoff("bull", "lead" if not fast else "pm")
        self._handoff("bear", "lead" if not fast else "pm")

        final_ask = ("최종 결정을 JSON 으로 내라. confidence·probability 는 0~1, 손절·익절은 % 양수, 시나리오 가격과 진입 가격대는 "
                     f"회의 자료의 현재가({brief.facts['close']:,})와 같은 통화·단위. summary·reasons·trust·worry·watch 는 각 3개, "
                     "short_args 는 3개. risk_votes 는 리스크 위원 3명의 의견. 근거가 약하면 Hold 를 택하라.")
        if fast:
            # 4~7) 대표가 팀장(투자 논문)·트레이더(실행안)·리스크 위원 3명(공격/중립/보수) 관점을 함께 검토
            for k in ("lead", "trader", "risk"):
                self._stage(k)
            self._stage("pm")
            self._say("pm", packet, "너는 이번 회의에서 리서치 팀장·트레이더·리스크 위원회(공격형/중립형/보수형)의 검토까지 "
                      "직접 맡는다. 투자 논문, 숏 논거와 반박, 시나리오, 분할 진입 가격대와 손절·익절, 세 리스크 위원의 의견을 "
                      "각각 따져본 뒤 " + final_ask, schema=FINAL_SCHEMA, working="최종 결정 중...",
                      also=FAST_MERGE["pm"])
            f = self.run["final"] or {}
            votes = f.get("risk_votes") or {}
            self._set("lead", bubble=f"등급 {RATING_KO.get(f.get('rating'), '-')} · 강세 {f.get('bull_score', '-')}점")
            self._set("trader", bubble=f"진입 {f.get('entry_low', '-')}~{f.get('entry_high', '-')} · 손절 -{f.get('stop_loss_pct', '-')}%")
            for k, n in (("aggr", "공격형"), ("neut", "중립형"), ("cons", "보수형")):
                self._set(k, bubble=f"{n} 의견: {votes.get(k, '-')}")
        else:
            self._stage("lead")
            plan = self._say("lead", packet, "투자 계획과 투자 논문을 써라.", working="토론 정리 중...")
            self._handoff("lead", "trader")
            self._stage("trader")
            trade = self._say("trader", packet, f"[투자 계획]\n{plan}\n\n실행안을 써라.", working="주문 설계 중...")
            for rid in ("aggr", "neut", "cons"):
                self._handoff("trader", rid)
            self._stage("risk")
            rctx = f"{packet}\n\n[투자 계획]\n{plan}\n\n[트레이더 실행안]\n{trade}"
            with ThreadPoolExecutor(3) as pool:
                fr = {rid: pool.submit(self._say, rid, rctx, "의견을 내라. 마지막 줄에 '의견: 확대/유지/축소/관망' 중 하나.",
                                       False, None, "위험 점검 중...") for rid in ("aggr", "neut", "cons")}
                risk = {rid: f.result() for rid, f in fr.items()}
            rhist = "".join(f"\n\n[{BY_ID[r].title}]\n{t}" for r, t in risk.items())
            self.run["risk_debate"] += [{"role": r, "round": 1, "text": t} for r, t in risk.items()]
            for rid in ("aggr", "neut", "cons"):
                self._handoff(rid, "pm")
            self._stage("pm")
            self._say("pm", f"{rctx}\n\n[리스크 위원회]{rhist}", final_ask, schema=FINAL_SCHEMA, working="최종 결정 중...")

        # 숫자 규칙 체크리스트와 실행 계획
        mc = self.cfg["markets"][self.market.value]
        self.run["final"] = principles.adjust_for_holding(self.run["final"], self.cfg["ai"].get("holding", "long"))
        cl = principles.checklist(self.run["final"], self.run["check"], bars, warnings, self.cfg["ai"].get("rules"),
                                  market_bars=principles.index_bars(self.data, self.market))
        self.run["checklist"] = cl
        self.run["plan"] = principles.trade_plan(self.run["final"], float(bars["close"].iloc[-1]), float(mc["budget"]),
                                                 float(self.cfg["risk"]["max_weight"]), cl["size"]["weight_factor"],
                                                 self.market.value)


def start(symbol: str, data, store: Store, cfg: dict, llm: LLM | None = None, toss=None,
          background: bool = True) -> dict:
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("종목을 입력하세요")
    existing = running_for(symbol)
    if existing:
        return get_run(existing)
    from .. import extras
    cap = float((cfg.get("ai") or {}).get("monthly_cost_cap_krw") or 0)
    spent = extras.month_cost_krw(store)
    if cap and spent >= cap:
        raise ValueError(f"이번 달 AI 비용 {spent:,.0f}원이 상한 {cap:,.0f}원에 닿아 회의를 열지 않습니다. 설정에서 상한을 바꿀 수 있습니다")
    m = Meeting(symbol, data, store, cfg, llm, toss)
    if background:
        threading.Thread(target=m.execute, name=f"meeting-{symbol}", daemon=True).start()
        return get_run(m.run["id"])
    return m.execute()


__all__ = ["Meeting", "start", "live_runs", "get_run", "LLMError"]
