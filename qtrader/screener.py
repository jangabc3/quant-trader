"""AI 회의 후보 자동 발굴. 사람이 종목을 정하지 않아도 매일 회의할 종목을 고른다.

후보 풀: 설정의 종목 + 관심 종목 + 지금 가진 종목 + 토스증권 거래대금 상위.
걸러내기: 매수 체크리스트의 숫자 규칙(200일선 위, 12개월·12-1개월 모멘텀 양수, 과열·변동성 상한)을 미리 본다.
순위: 변동성 대비 모멘텀(12-1개월 수익률 ÷ 연 변동성). 가진 종목은 손절·보유 판단을 위해 항상 넣는다.
AI 회의는 비용이 들어서, 숫자로 이미 탈락한 종목에는 회의를 열지 않는다.
"""
from __future__ import annotations

import json

from . import principles
from .models import Market


def pool(ctx, market: Market) -> tuple[list[str], set[str]]:
    mc = ctx.cfg["markets"][market.value]
    kr = market == Market.KR
    mine = lambda s: s.split(":")[-1][:1].isdigit() == kr
    held = set()
    try:
        held = set(ctx.broker.account(market).positions)
    except Exception:
        pass
    syms = list(mc.get("universe", [])) + [s for s in ctx.cfg.get("watchlist", []) if mine(s)] + sorted(held)
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is not None:
        from . import market as market_mod
        try:
            syms += [x["symbol"] for x in market_mod.rankings(client, "amount", market.value, "1d", 30)["items"]]
        except Exception:
            pass
    return list(dict.fromkeys(syms))[:60], held


def candidates(ctx, market: Market, n: int) -> list[dict]:
    """[{symbol, score, reason, held}] 좋은 순. 가진 종목이 먼저."""
    R = principles.DEFAULT_RULES | (ctx.cfg["ai"].get("rules") or {})
    syms, held = pool(ctx, market)
    out = []
    for s in syms:
        try:
            bars = ctx.data.bars(market, s, 300)
            if len(bars) < 60:
                continue
            sig = principles.market_signals(bars)
        except Exception:
            continue
        fails = []
        if R["require_trend"] and sig["last"] <= sig["sma200"]:
            fails.append("200일선 아래")
        if R["require_momentum"] and (sig["ret12"] <= 0 or sig["mom12_1"] <= 0):
            fails.append("모멘텀 음수")
        if sig["rsi"] >= R["max_rsi"]:
            fails.append(f"과열 RSI {sig['rsi']:.0f}")
        if sig["vol"] > R["max_vol"]:
            fails.append(f"변동성 {sig['vol']:.0%}")
        score = sig["mom12_1"] / max(sig["vol"], 0.05)
        reason = (f"12-1개월 {sig['mom12_1']:+.0%} · 변동성 {sig['vol']:.0%} · 200일선 {sig['last'] / sig['sma200'] - 1:+.0%}")
        if s in held:
            out.append({"symbol": s, "score": score, "reason": "보유 중 · " + reason, "held": True, "fails": fails})
        elif not fails:
            out.append({"symbol": s, "score": score, "reason": reason, "held": False, "fails": []})
    out.sort(key=lambda x: (not x["held"], -x["score"]))
    keep = [x for x in out if x["held"]] + [x for x in out if not x["held"]][:max(0, n - sum(x["held"] for x in out))]
    return keep


def todays(ctx, market: Market, today: str, n: int) -> list[dict]:
    """하루 한 번 고르고 저장해 둔다 (화면·엔진이 같은 목록을 본다)."""
    key = f"picks_{market.value}_{today}"
    saved = ctx.store.get(key)
    if saved:
        return json.loads(saved)
    picks = candidates(ctx, market, n)
    ctx.store.set(key, json.dumps(picks, ensure_ascii=False))
    ctx.store.log("info", f"picks.{market.value}", "오늘 AI 회의 후보: " + ", ".join(p["symbol"] for p in picks) if picks
                  else "오늘은 숫자 규칙을 통과한 후보가 없어 회의를 열지 않습니다")
    return picks
