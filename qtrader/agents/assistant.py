"""내 AI 비서: 대화로 시장·계좌·종목을 묻고, AI 회의를 요청한다. 주문은 직접 내지 않는다(엔진과 리스크 관리만 주문)."""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime

import anthropic

from .. import market as market_mod
from . import context as ctxmod
from . import meeting
from .llm import BETAS, WEB_SEARCH, LLMError, make_client
from .roles import PROFILE_DEFAULT

SYSTEM = (
    "너는 QTrader 의 AI 애널리스트 '큐비'다. 한국어로 답한다. 증권사 리서치 애널리스트처럼 숫자와 근거로 말한다. "
    "종목 질문을 받으면 먼저 ai_decision 으로 그 종목의 최근 AI 투자 회의 결론(등급·확신도·진입/손절/익절 가격·체크리스트)을 확인하고, "
    "회의가 있으면 그 결론을 중심으로, 없으면 chart_brief·quotes 로 확인한 숫자로 답하며 회의를 열지 제안한다. "
    "답변 형식: 첫 줄에 **결론** 한 문장 → **근거** 2~4개(숫자 포함) → **리스크** 1~3개 → 해당되면 **실행 계획**(가격·손절·익절). "
    "사용자가 지금 보고 있는 화면 정보가 주어지면 '이 종목' 같은 말은 그 종목으로 이해한다. "
    "시세·계좌·차트 숫자는 반드시 도구로 확인한 값만 말하고, 확인 못 한 것은 모른다고 말한다. "
    "투자 판단을 요청받으면 근거와 위험을 함께 말하고, 최종 판단은 사용자 몫이라고 알린다. "
    "너는 주문을 낼 수 없다. 매매는 QTrader 엔진이 설정한 전략과 리스크 한도 안에서만 한다. "
    "깊은 분석이 필요하면 start_meeting 으로 AI 투자 회의를 열 수 있다 (몇 분 걸리고 API 비용이 든다 - 열기 전에 알린다). "
    "'왜 샀어?', '수익 얼마야?', '오늘 뭐 살 거야?', '내 기준이 뭐야?', '요즘 뜨는 업종은?' 같은 질문은 trades_why·performance·today_picks·"
    "my_rules·industries·investor_flows·live_board·stock_news 도구로 확인해서 답한다. 앱 사용법을 물으면 app_help 를 본다. "
    "초보자도 이해하도록 어려운 말은 풀어서 쓰고, 숫자에는 단위(원·$·%)를 붙인다."
)

APP_HELP = """QTrader 화면 안내
- 시장: 맨 위 내 요약, 코스피·코스닥/미국 실제 지수, 실시간 차트(관심·거래대금·거래량·급상승·급하락), 지금 뜨는 산업, 외국인·기관 매매 동향
- 종목: 차트·호가·체결·재무·투자자 매매·뉴스, 오른쪽 주문창에서 직접 구매·판매 (손절·익절도 함께 정할 수 있음, 정정·조건 주문)
- AI 사무실: 실시간(장중 감시·후보 계산을 캐릭터로) / 회의 보기, 오늘 AI가 고른 회의 후보와 고른 기준, 회의 시연
- 내 계좌: 보유 종목 · 수익(수익 리포트, 지수 대비) · 거래 기록(왜 사고팔았나요, 내보내기) · 하루 리포트 · 알림
- 설정: 운용 모드(가상매매/토스 실전), 실전 주문 PIN, API 키와 연결 테스트, 매매 기간(AI에게 맡기기/장기/스윙/단기), 쓸 돈, 알림·비용 상한
- 자동매매 순서: 실행 40분 전 AI 회의 후보 선정 → 회의 → 체크리스트 통과 종목만 주문 → 장중 2분마다 손절·익절 감시 → 장 마감 요약
- 긴급 정지: 새로 사는 것만 멈춤 (손절·익절 매도는 계속). 하루 손실 한도를 넘으면 자동으로 켜짐"""


def _tool(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": desc, "strict": True,
            "input_schema": {"type": "object", "properties": props, "required": required,
                             "additionalProperties": False}}


SYM = {"type": "string", "description": "국내 6자리 코드(005930) 또는 미국 티커(AAPL)"}
TOOLS = [
    _tool("market_overview", "코스피·코스닥 지수, 투자자별 순매수(억원), 환율, 국채금리, 미국 지수 ETF 를 본다.", {}, []),
    _tool("rankings", "실시간/일간 종목 순위.", {
        "kind": {"type": "string", "enum": list(market_mod.RANK_TYPES)},
        "market": {"type": "string", "enum": ["KR", "US"]}}, ["kind", "market"]),
    _tool("quotes", "종목 현재가와 전일 대비 등락.", {"symbols": {"type": "array", "items": SYM}}, ["symbols"]),
    _tool("chart_brief", "한 종목의 수익률·이동평균·RSI·볼린저·변동성·수급 요약.", {"symbol": SYM}, ["symbol"]),
    _tool("portfolio", "내 계좌(현재 운용 모드)의 현금과 보유 종목.", {}, []),
    _tool("recent_orders", "최근 주문 내역.", {"limit": {"type": "integer"}}, ["limit"]),
    _tool("ai_decision", "그 종목의 가장 최근 AI 투자 회의 결론.", {"symbol": SYM}, ["symbol"]),
    _tool("start_meeting", "그 종목으로 AI 투자 회의를 연다. 몇 분 걸리고 비용이 든다.", {"symbol": SYM}, ["symbol"]),
    _tool("today_picks", "오늘 AI 회의 후보와 고른 이유(멀티팩터 점수·공시·재무).", {"market": {"type": "string", "enum": ["KR", "US"]}}, ["market"]),
    _tool("performance", "수익 리포트: 총 손익·승률·손익비·최대 낙폭·거래별 결과.", {"market": {"type": "string", "enum": ["KR", "US"]}}, ["market"]),
    _tool("trades_why", "최근 사고판 거래와 그 근거(AI 회의 결론·체크리스트·손절·익절).", {"limit": {"type": "integer"}}, ["limit"]),
    _tool("my_rules", "내 투자 성향과 자동매매 기준(비중·손실 한도·매수 조건·매매 기간).", {}, []),
    _tool("live_board", "장중 감시 중인 종목(손절·익절까지 거리)·다음 후보·오늘 한 일·다음 일정.", {"market": {"type": "string", "enum": ["KR", "US"]}}, ["market"]),
    _tool("industries", "지금 뜨는 산업(섹터) 순위.", {"market": {"type": "string", "enum": ["KR", "US"]}}, ["market"]),
    _tool("investor_flows", "국내 외국인·기관·개인 순매수 상위 종목.", {}, []),
    _tool("stock_news", "그 종목의 최신 뉴스 제목.", {"symbol": SYM}, ["symbol"]),
    _tool("app_help", "QTrader 화면과 기능 사용법.", {}, []),
]


class Assistant:
    def __init__(self, get_ctx, get_toss, cfg_loader, client=None):
        self.get_ctx, self.get_toss, self.cfg_loader = get_ctx, get_toss, cfg_loader
        self._client = client
        self.threads: dict[str, list] = {}
        self.lock = threading.Lock()

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    # 도구 실행
    def call(self, name: str, args: dict) -> str:
        t = self.get_toss()
        if name == "market_overview":
            ov = market_mod.overview(t)
            for i in ov.get("indices", []) + ov.get("us", []) + ov.get("us_index", []):
                i.pop("spark", None)
            return json.dumps(ov, ensure_ascii=False)
        if name == "rankings":
            r = market_mod.rankings(t, args["kind"], args["market"], count=15)
            return json.dumps(r, ensure_ascii=False)
        if name == "quotes":
            if t is None:
                ctx = self.get_ctx()
                out = []
                for s in args["symbols"][:20]:
                    try:
                        out.append({"symbol": s, "price": ctx.data.price(ctxmod.market_of(s), s)})
                    except Exception as e:
                        out.append({"symbol": s, "error": str(e)})
                return json.dumps(out, ensure_ascii=False)
            return json.dumps(market_mod.watch_quotes(t, args["symbols"][:20]), ensure_ascii=False)
        if name == "chart_brief":
            s = args["symbol"].upper()
            ctx = self.get_ctx()
            bars = ctx.data.bars(ctxmod.market_of(s), s, 260)
            flows = None
            if t is not None and ctxmod.market_of(s).value == "KR":
                try:
                    flows = t.stock_investor_trading(s, 5)
                except Exception:
                    flows = None
            return ctxmod.build(s, bars, s, flows).text()
        if name == "portfolio":
            ctx = self.get_ctx()
            out = {"mode": ctx.cfg["mode"], "accounts": []}
            for m, mc in ctx.cfg["markets"].items():
                if not mc["enabled"]:
                    continue
                a = ctx.broker.account(ctxmod.Market(m))
                out["accounts"].append({"market": m, "cash": a.cash, "equity": a.equity, "positions": [
                    {"symbol": p.symbol, "qty": p.qty, "avg": p.avg_price, "last": p.last_price} for p in a.positions.values()]})
            return json.dumps(out, ensure_ascii=False)
        if name == "recent_orders":
            return json.dumps(self.get_ctx().store.orders(min(int(args["limit"]), 50)), ensure_ascii=False)
        if name == "ai_decision":
            rows = self.get_ctx().store.ai_runs(1, args["symbol"].upper())
            if not rows:
                return "이 종목의 AI 회의 기록이 없습니다."
            r = rows[0]
            cl = r.get("checklist") or {}
            return json.dumps({"date": r["trade_date"], "status": r["status"], "final": r["final"], "plan": r.get("plan"),
                               "checklist_passed": cl.get("passed"),
                               "checklist_failed": [x["name"] for x in cl.get("rules", []) if not x["ok"]],
                               "fact_check_summary": (r.get("check") or {}).get("summary"),
                               "error": r.get("error")}, ensure_ascii=False)
        if name == "start_meeting":
            ctx = self.get_ctx()
            run = meeting.start(args["symbol"], ctx.data, ctx.store, ctx.cfg, toss=t)
            return f"AI 투자 회의를 시작했습니다 (회의 번호 {run['id']}). 'AI 사무실' 화면에서 진행 상황을 볼 수 있습니다."
        if name in ("today_picks", "performance", "live_board"):
            from .. import extras, live, screener
            from ..engine import trade_date
            ctx = self.get_ctx()
            m = ctxmod.Market(args["market"])
            if name == "today_picks":
                return json.dumps({"기준": screener.__doc__, "후보": screener.todays(ctx, m, trade_date(m), int(ctx.cfg["ai"]["max_symbols_per_day"]))},
                                  ensure_ascii=False)[:15000]
            if name == "performance":
                r = extras.performance(ctx.store, ctx.broker.name, m.value)
                return json.dumps({"summary": r["summary"], "trades": r["trades"][:15]}, ensure_ascii=False, default=str)
            return json.dumps(live.snapshot(ctx, m), ensure_ascii=False, default=str)[:15000]
        if name == "trades_why":
            ctx = self.get_ctx()
            rows = ctx.store.rows("select trade_date, market, symbol, side, qty, price, status, strategy, message from orders "
                                  "where status in ('filled','submitted') order by id desc limit ?", (min(int(args["limit"]), 30),))
            return json.dumps(rows, ensure_ascii=False)
        if name == "my_rules":
            from .. import profile as profile_mod
            cfg = self.cfg_loader()
            return json.dumps({"investor": cfg.get("investor"), "criteria": profile_mod.criteria(cfg), "holding": cfg["ai"].get("holding"),
                               "mode": cfg["mode"]}, ensure_ascii=False)
        if name == "industries":
            return json.dumps(market_mod.industries(t, args["market"]), ensure_ascii=False)
        if name == "investor_flows":
            return json.dumps(market_mod.investor_flows(t), ensure_ascii=False)
        if name == "stock_news":
            from .. import news as news_mod
            from .. import stockdata
            s = args["symbol"].upper()
            try:
                nm = stockdata.summary(t, s).get("name") or s
            except Exception:
                nm = s
            return json.dumps([{"title": x["title"], "time": x.get("time"), "source": x.get("source")} for x in news_mod.stock_news(nm, 10)],
                              ensure_ascii=False)
        if name == "app_help":
            return APP_HELP
        raise ValueError(f"알 수 없는 도구 {name}")

    def chat(self, thread_id: str | None, text: str, page: str = "") -> dict:
        cfg = self.cfg_loader()
        ai = cfg["ai"]
        tid = thread_id or uuid.uuid4().hex[:10]
        with self.lock:
            msgs = self.threads.setdefault(tid, [])
            ctx_line = f" ({page})" if page else ""
            msgs.append({"role": "user", "content": f"[{datetime.now():%Y-%m-%d %H:%M}{ctx_line}] {text}"})
        used: list[str] = []
        sources: list[dict] = []
        tools = TOOLS + ([WEB_SEARCH] if ai.get("web_research", True) else [])
        for _ in range(8):
            try:
                resp = self.client.beta.messages.create(
                    model=ai.get("quick_model") or ai.get("model"), max_tokens=8000,
                    system=f"{SYSTEM}\n\n# 사용자 투자 프로필\n{ai.get('profile') or PROFILE_DEFAULT}",
                    messages=msgs, tools=tools,
                    output_config={"effort": "low"}, betas=BETAS, fallbacks="default")
            except anthropic.AuthenticationError:
                raise LLMError("Claude API 키가 올바르지 않습니다. 설정에서 키를 확인하세요")
            except anthropic.RateLimitError:
                raise LLMError("Claude API 호출 한도에 걸렸습니다. 잠시 후 다시 시도하세요")
            except anthropic.APIStatusError as e:
                raise LLMError(f"Claude API 오류 {e.status_code}: {e.message}")
            except anthropic.APIConnectionError:
                raise LLMError("Claude API 에 연결할 수 없습니다")
            msgs.append({"role": "assistant", "content": resp.content})
            for b in resp.content:
                if b.type == "web_search_tool_result" and isinstance(b.content, list):
                    sources += [{"title": r.title, "url": r.url} for r in b.content]
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason != "tool_use" or not calls:
                answer = "".join(b.text for b in resp.content if b.type == "text").strip()
                if resp.stop_reason == "refusal":
                    answer = "이 요청에는 답할 수 없습니다."
                return {"thread_id": tid, "answer": answer, "tools": used, "sources": sources[:8]}
            results = []
            for c in calls:
                used.append(c.name)
                try:
                    out, err = self.call(c.name, dict(c.input)), False
                except Exception as e:
                    out, err = f"도구 실행 실패: {e}", True
                results.append({"type": "tool_result", "tool_use_id": c.id, "content": out[:20000], "is_error": err})
            msgs.append({"role": "user", "content": results})
        return {"thread_id": tid, "answer": "확인할 것이 너무 많아 멈췄습니다. 질문을 나눠 주세요.", "tools": used,
                "sources": sources[:8]}
