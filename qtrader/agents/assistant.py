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

SYSTEM = (
    "너는 QTrader 사용자의 개인 투자 비서 '비서'다. 한국어로 짧고 분명하게 답한다. "
    "시세·계좌·차트 숫자는 반드시 도구로 확인한 값만 말하고, 확인 못 한 것은 모른다고 말한다. "
    "투자 판단을 요청받으면 근거와 위험을 함께 말하고, 최종 판단은 사용자 몫이라고 알린다. "
    "너는 주문을 낼 수 없다. 매매는 QTrader 엔진이 설정한 전략과 리스크 한도 안에서만 한다. "
    "깊은 분석이 필요하면 start_meeting 으로 AI 투자 회의를 열 수 있다 (몇 분 걸리고 API 비용이 든다 - 열기 전에 알린다)."
)


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
            for i in ov.get("indices", []):
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
            return json.dumps({"date": r["trade_date"], "status": r["status"], "final": r["final"],
                               "error": r.get("error")}, ensure_ascii=False)
        if name == "start_meeting":
            ctx = self.get_ctx()
            run = meeting.start(args["symbol"], ctx.data, ctx.store, ctx.cfg, toss=t)
            return f"AI 투자 회의를 시작했습니다 (회의 번호 {run['id']}). 'AI 사무실' 화면에서 진행 상황을 볼 수 있습니다."
        raise ValueError(f"알 수 없는 도구 {name}")

    def chat(self, thread_id: str | None, text: str) -> dict:
        cfg = self.cfg_loader()
        ai = cfg["ai"]
        tid = thread_id or uuid.uuid4().hex[:10]
        with self.lock:
            msgs = self.threads.setdefault(tid, [])
            msgs.append({"role": "user", "content": f"[{datetime.now():%Y-%m-%d %H:%M}] {text}"})
        used: list[str] = []
        sources: list[dict] = []
        tools = TOOLS + ([WEB_SEARCH] if ai.get("web_research", True) else [])
        for _ in range(8):
            try:
                resp = self.client.beta.messages.create(
                    model=ai["model"], max_tokens=8000, system=SYSTEM, messages=msgs, tools=tools,
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
