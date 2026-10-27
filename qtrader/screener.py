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


# 후보를 어디서 데려왔는지 (화면의 '이유'에 붙는다)
ORIGIN: dict[str, str] = {}


def pool(ctx, market: Market) -> tuple[list[str], set[str]]:
    """대형 우량주만 보지 않도록 여러 곳에서 후보를 모은다. 숫자 규칙은 뒤에서 똑같이 건다."""
    mc = ctx.cfg["markets"][market.value]
    kr = market == Market.KR
    mine = lambda s: s.split(":")[-1][:1].isdigit() == kr
    held = set()
    try:
        held = set(ctx.broker.account(market).positions)
    except Exception:
        pass
    tagged: list[tuple[str, str]] = [(s, "보유") for s in sorted(held)]
    tagged += [(s, "관심") for s in ctx.cfg.get("watchlist", []) if mine(s)]
    tagged += [(s, "기본 목록") for s in mc.get("universe", [])]
    client = getattr(ctx.data, "client", None) if getattr(ctx.data, "name", "") == "toss" else None
    if client is not None:
        from . import market as market_mod
        for kind, dur, n, tag in (("amount", "1d", 25, "거래대금 상위"), ("gainers", "1w", 15, "1주 급상승"),
                                  ("gainers", "1mo", 15, "1개월 급상승 (성장·테마)"), ("volume", "1d", 10, "거래량 급증")):
            try:
                tagged += [(x["symbol"], tag) for x in market_mod.rankings(client, kind, market.value, dur, n)["items"]]
            except Exception:
                pass
        try:  # 오늘 가장 강한 섹터 3곳의 거래대금 상위 종목 (요즘 뜨는 업종)
            for sec in client.sector_rankings("TOP_GAINERS", market.value, "1w")[:3]:
                for x in client.sector_stocks(sec.get("sectorId"), market.value)[:4]:
                    tagged.append((x["symbol"], f"강세 섹터 · {sec.get('name', '')}"))
        except Exception:
            pass
    out = []
    for s, tag in tagged:
        if s not in ORIGIN or tag == "보유":
            ORIGIN[s] = tag
        out.append(s)
    return list(dict.fromkeys(out))[:80], held


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
        reason = (f"{ORIGIN.get(s, '')} · 12-1개월 {sig['mom12_1']:+.0%} · 변동성 {sig['vol']:.0%}").lstrip(" ·")
        if s in held:
            out.append({"symbol": s, "score": score, "reason": reason, "held": True, "fails": fails})
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
