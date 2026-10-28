"""사는 순간 잡기 (숫자 규칙, AI 호출 없음) + 신호가 뜬 종목만 AI 거부권 한 번.

예전에는 15분마다 후보 순서대로 14명 회의(약 950원)를 열고 결론이 매수면 샀다. 사는 시점이 'AI 차례'였고,
보류로 끝나도 같은 돈이 나갔다. 지금은
1) 2분마다 오늘 후보의 실시간 가격을 보고 아래 신호가 뜨는지 본다 (무료)
   - 눌림목 반등: 오르는 추세(200일선 위, 20일선 > 60일선)에서 20일선까지 내려왔다가 어제 고가를 넘어 다시 오를 때
     (추세 추종 + 단기 되돌림: Jegadeesh 1990 단기 반전, Moskowitz·Ooi·Pedersen 2012 시계열 모멘텀)
   - 거래량 돌파: 200일선 위에서 20일 최고가를 넘고, 오늘 거래량이 평소보다 1.5배 넘게 빠를 때
     (George & Hwang 2004 52주·신고가 근처 모멘텀, 거래량 동반 돌파)
   - 너무 멀리 오른 자리(20일선보다 6%·12% 넘게 위)·과열(RSI 눌림목 75·돌파 80 이상)이면 신호로 보지 않는다
2) 신호가 뜬 종목만 베어리(약세 연구원)가 '사면 안 되는 이유'를 한 번 찾는다 (Sonnet 1번, 웹 검색 없이
   DART 공시 제목·뉴스 제목·투자 유의 지정만 읽는다, 약 10~30원)
3) 거부하지 않으면 숫자 안전 점검(principles.checklist, kind="signal")을 거쳐 그 종목만 산다 (engine.buy_one)
손절은 가격 변동폭(ATR)의 2배 아래(3~10% 사이), 익절은 그 2배 위 (손익비 2 : 1).
확실한 타이밍은 없다. 신호는 '살 만한 자리'를 좁히는 것이고, 손실은 손절선이 작게 끊는다.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pandas as pd

from . import indicators as ind
from .models import Market

KINDS = {"pullback": "눌림목 반등", "breakout": "거래량 돌파"}
_bars: dict[tuple, pd.DataFrame] = {}  # (시장, 종목, 날짜) -> 일봉 (하루 한 번만 받는다)

VETO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["veto", "confidence", "reason", "risks", "positives"],
    "properties": {
        "veto": {"type": "boolean", "description": "사면 안 되는 구체적 위험 신호가 있으면 true"},
        "confidence": {"type": "number", "description": "0~1. 위험 신호가 없고 숫자가 좋으면 0.65~0.8, 애매하면 낮게"},
        "reason": {"type": "string", "description": "40자 이내 한국어 한 줄 결론"},
        "risks": {"type": "array", "items": {"type": "string"}, "description": "찾은 위험 (없으면 빈 목록)"},
        "positives": {"type": "array", "items": {"type": "string"}, "description": "좋은 점 1~2개"},
    },
}


def split_today(bars: pd.DataFrame, today: str) -> tuple[pd.DataFrame, pd.Series | None]:
    """일봉을 '어제까지'와 '오늘 진행 중인 봉'으로 나눈다 (토스 일봉은 장중에 오늘 봉이 같이 온다)."""
    if len(bars) and str(bars.index[-1])[:10] == today:
        return bars.iloc[:-1], bars.iloc[-1]
    return bars, None


def session_fraction(market: Market, now: datetime | None = None) -> float:
    """정규장 중 지난 비율 (거래량 속도 계산용). 장 전·후는 1."""
    from .engine import TZ
    t = (now or datetime.now(timezone.utc)).astimezone(TZ[market])
    start, end = ((9, 0), (15, 30)) if market == Market.KR else ((9, 30), (16, 0))
    s, e = t.replace(hour=start[0], minute=start[1], second=0), t.replace(hour=end[0], minute=end[1], second=0)
    if t <= s or t >= e:
        return 1.0
    return max(0.05, (t - s).total_seconds() / (e - s).total_seconds())


def check(bars: pd.DataFrame, price: float, today: str, frac: float = 1.0) -> dict | None:
    """신호가 뜨면 {kind, name, why, atr, stop, take, stop_pct, take_pct}, 아니면 None. bars 는 오늘 봉이 있어도 된다."""
    hist, now_bar = split_today(bars, today)
    if len(hist) < 200 or not price or price <= 0:
        return None
    c, h, lo, v = hist["close"], hist["high"], hist["low"], hist["volume"]
    sma20, sma60, sma200 = (float(ind.sma(c, n).iloc[-1]) for n in (20, 60, 200))
    atr = float(ind.atr(h, lo, c).iloc[-1])
    rsi = float(ind.rsi(pd.concat([c, pd.Series([price])], ignore_index=True)).iloc[-1])
    if rsi >= 80 or atr <= 0:  # 눌림목은 75, 돌파는 80 미만 (돌파는 원래 RSI 가 높다. 대신 20일선에서 12% 넘게 멀면 안 산다)
        return None
    sig = None
    high20 = float(h.tail(20).max())
    vol_today = float(now_bar["volume"]) if now_bar is not None and "volume" in now_bar else 0.0
    avg20 = float(v.tail(20).mean()) or 0.0
    pace = vol_today / frac / avg20 if avg20 and vol_today else 0.0
    # 둘 다 맞으면 거래량이 받쳐 주는 돌파를 먼저
    if price > sma200 and price > high20 and pace >= 1.5 and price <= sma20 * 1.12:
        sig = ("breakout", f"20일 최고가({high20:,.0f})를 넘었고 거래량이 평소의 {pace:.1f}배 속도예요")
    up = price > sma200 and sma20 > sma60
    touched = float(lo.tail(3).min()) <= sma20 * 1.01
    if not sig and up and touched and rsi < 75 and price > float(h.iloc[-1]) and sma20 < price <= sma20 * 1.06:
        sig = ("pullback", f"오르는 추세에서 20일선({sma20:,.0f})까지 눌렸다가 어제 고가({float(h.iloc[-1]):,.0f})를 넘어 다시 올라요")
    if not sig:
        return None
    stop_pct = min(10.0, max(3.0, 2 * atr / price * 100))
    return {"kind": sig[0], "name": KINDS[sig[0]], "why": sig[1], "price": price, "atr": round(atr, 4), "rsi": round(rsi, 1),
            "stop_pct": round(stop_pct, 2), "take_pct": round(stop_pct * 2, 2), "pace": round(pace, 2),
            "sma20": round(sma20, 4), "sma200": round(sma200, 4)}


def daily_bars(ctx, market: Market, symbol: str, today: str) -> pd.DataFrame:
    """일봉은 하루에 한 번만 받고 (토스 호출 아끼기), 오늘 봉의 거래량은 2분마다 다시 받지 않는다 (가격은 실시간으로 따로)."""
    k = (market.value, symbol, today)
    hit = _bars.get(k)
    if hit is None or time.monotonic() - hit.attrs.get("_at", 0) > 600:  # 10분마다 오늘 봉(거래량)만 새로
        hit = ctx.data.bars(market, symbol, 300)
        hit.attrs["_at"] = time.monotonic()
        for old in [x for x in _bars if x[2] != today]:
            _bars.pop(old, None)
        _bars[k] = hit
    return hit


def live_prices(ctx, market: Market, symbols: list[str]) -> dict[str, float]:
    """후보의 실시간 현재가를 한 번에 (토스면 묶음 호출 하나)."""
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is not None:
        try:
            got = client.prices(symbols)
            return {s: float(got.get(s) or got.get(s.split(":")[-1]) or 0) for s in symbols}
        except Exception:
            pass
    out = {}
    for s in symbols:
        try:
            out[s] = float(ctx.data.price(market, s))
        except Exception:
            pass
    return out


def scan(ctx, market: Market, symbols: list[str], today: str) -> list[dict]:
    """후보들 중 지금 신호가 뜬 종목 (가격 하나로 여러 종목을 본다)."""
    px = live_prices(ctx, market, symbols)
    frac = session_fraction(market)
    hits = []
    for s in symbols:
        try:
            sig = check(daily_bars(ctx, market, s, today), px.get(s, 0), today, frac)
        except Exception:
            sig = None
        if sig:
            hits.append(sig | {"symbol": s})
    return hits


def veto_context(ctx, market: Market, symbol: str, name: str, sig: dict) -> tuple[str, list[dict]]:
    """거부권 판단 자료: 신호·숫자 + DART 공시 제목 + 뉴스 제목 + 투자 유의 지정 (모두 무료, 웹 검색 없음)."""
    from . import config as config_mod
    from .news import stock_news
    from .screener import disclosure_titles
    warns = []
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is not None:
        try:
            warns = client.warnings(symbol)
        except Exception:
            warns = []
    try:
        titles = disclosure_titles(symbol, config_mod.home(), 20)[:15]
    except Exception:
        titles = []
    try:
        news = [f"{n.get('published', '')[:10]} {n.get('title', '')}" for n in stock_news(name, 12)]
    except Exception:
        news = []
    cur = "원" if market == Market.KR else "달러"
    text = (f"종목: {name} ({symbol})\n신호: {sig['name']} — {sig['why']}\n"
            f"현재가 {sig['price']:,} {cur} · RSI {sig['rsi']} · 20일선 {sig['sma20']:,.2f} · 200일선 {sig['sma200']:,.2f}\n"
            f"계획: 손절 -{sig['stop_pct']}% · 익절 +{sig['take_pct']}%\n"
            f"거래소 투자 유의 지정: {', '.join(w.get('warningType', '') for w in warns) or '없음'}\n"
            f"DART 최근 20일 공시 제목:\n" + ("\n".join(f"- {t}" for t in titles) or "- (없음 또는 DART 키 없음)") +
            "\n최근 뉴스 제목:\n" + ("\n".join(f"- {t}" for t in news) or "- (못 받음)"))
    return text, warns


VETO_ASK = ("너는 QTrader 의 약세 연구원 베어리다. 숫자 규칙이 '살 만한 자리'라고 신호를 냈다. 아래 자료만 보고 "
            "'사면 안 되는 구체적 위험 신호'가 있는지 판단하라. 위험 신호의 예: 유상증자·전환사채·감자·횡령·불성실공시·"
            "거래정지·관리종목 같은 악재 공시, 실적 쇼크·가이던스 하향 뉴스, 투자 주의·경고 지정, 며칠 사이 급등으로 과열, "
            "곧 있을 실적 발표 같은 큰 이벤트. 자료에 없는 것을 지어내지 않는다. 뉴스를 못 받았다는 것만으로 거부하지 않는다. "
            "위험 신호가 없으면 veto=false. 결과는 지정된 JSON 형식으로만.")


def ai_ready() -> bool:
    """Claude 키가 있는지. 없으면 거부권을 묻지 않는다 (물어도 실패해서 사지 못하고, 하루 확인 수만 줄어든다)."""
    import os
    from . import vault
    return bool(vault.get_anthropic() or os.environ.get("ANTHROPIC_API_KEY"))


def veto(ctx, market: Market, symbol: str, name: str, sig: dict, llm=None) -> dict:
    """베어리의 거부권 한 번. 실패하면 사지 않는다 (veto=True 로 본다)."""
    from .agents.llm import LLM, LLMError
    from .agents.roles import common_system
    ai = ctx.cfg["ai"]
    text, warns = veto_context(ctx, market, symbol, name, sig)
    try:
        rep = (llm or LLM(ai.get("quick_model") or ai.get("model"), "low")).run(
            common_system(ai.get("profile", "")), VETO_ASK, schema=VETO_SCHEMA, max_tokens=4000, context=text, effort="low")
        d = dict(rep.data or {})
        cost = rep.cost_usd()
    except Exception as e:  # LLMError 는 이미 쉬운 말, 그 밖(키·연결 문제 등)은 영어 오류를 그대로 보이지 않는다
        why = str(e)[:60] if isinstance(e, LLMError) else "Claude 에 연결하지 못했어요 (키·인터넷 확인)"
        d, cost = {"veto": True, "confidence": 0, "reason": f"AI 확인 실패라 사지 않아요: {why}", "risks": [], "positives": []}, 0.0
    d["cost_usd"] = round(cost, 5)
    d["context"] = text
    d["warnings"] = warns
    return d


def save_decision(ctx, market: Market, symbol: str, name: str, sig: dict, v: dict, today: str) -> dict:
    """신호 + 거부권 결과를 AI 판단 기록(ai_runs)에 남긴다. 매수(engine.buy_one)·채점표·화면이 회의 결과처럼 읽는다."""
    from . import principles
    from .storage import now
    final = {"rating": "Hold" if v.get("veto") else "Overweight", "confidence": float(v.get("confidence") or 0),
             "headline": v.get("reason", ""), "summary": [sig["why"]] + list(v.get("positives") or [])[:2],
             "worry": list(v.get("risks") or [])[:3], "stop_loss_pct": sig["stop_pct"], "take_profit_pct": sig["take_pct"],
             "entry_low": sig["price"], "entry_high": sig["price"], "horizon_days": 20}
    bars = daily_bars(ctx, market, symbol, today)
    mc = ctx.cfg["markets"][market.value]
    cl = principles.checklist(final, None, bars, v.get("warnings") or [], principles.rules_for(ctx.cfg),
                              market_bars=principles.index_bars(ctx.data, market), kind="signal")
    plan = principles.trade_plan(final, sig["price"], float(mc["budget"]), float(ctx.cfg["risk"]["max_weight"]),
                                 cl["size"]["weight_factor"], market.value)
    report = (f"한줄요약: {v.get('reason', '')}\n\n**신호** {sig['name']} — {sig['why']}\n\n**거부권** "
              + ("거부 (사지 않음)" if v.get("veto") else "통과 (뚜렷한 위험 신호 없음)")
              + (("\n\n**찾은 위험**\n" + "\n".join(f"- {x}" for x in v["risks"])) if v.get("risks") else "")
              + (("\n\n**좋은 점**\n" + "\n".join(f"- {x}" for x in v["positives"])) if v.get("positives") else "")
              + f"\n\n---\n**읽은 자료**\n{v.get('context', '')}")
    run = {"id": uuid.uuid4().hex[:12], "symbol": symbol, "market": market.value, "name": name, "mode": "signal",
           "origin": "signal", "trade_date": today, "started_at": now(), "finished_at": now(), "status": "done",
           "stage": "완료", "final": final, "check": None, "plan": plan, "checklist": cl, "signal": sig,
           "cost_usd": v.get("cost_usd", 0.0), "calls": 1 if v.get("cost_usd") else 0,
           "agents": {"bear": {"status": "done", "bubble": v.get("reason", ""), "report": report, "sources": [], "tokens": 0,
                               "cost": v.get("cost_usd", 0.0)}},
           "stages": {}, "facts": {"close": sig["price"], "rsi14": sig["rsi"]}}
    ctx.store.save_ai_run(run)
    return run


def veto_today(store, market: Market, today: str) -> int:
    """오늘 이 시장에서 쓴 거부권 확인 수 (하루 한도용)."""
    return sum(1 for r in store.ai_runs(300) if r["trade_date"] == today and r["market"] == market.value
               and r.get("origin") == "signal")


def last_signals(store, market: Market, today: str) -> list[dict]:
    """화면용: 오늘 뜬 신호와 거부권 결과."""
    out = []
    for r in store.ai_runs(300):
        if r["trade_date"] != today or r["market"] != market.value or r.get("origin") != "signal":
            continue
        f = r.get("final") or {}
        out.append({"symbol": r["symbol"], "name": r.get("name"), "at": r["started_at"], "signal": r.get("signal") or {},
                    "veto": f.get("rating") == "Hold", "reason": f.get("headline", ""), "run_id": r["id"],
                    "passed": bool((r.get("checklist") or {}).get("passed"))})
    return out


__all__ = ["check", "scan", "veto", "save_decision", "veto_today", "last_signals", "KINDS"]
