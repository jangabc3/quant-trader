"""사는 신호 + 팔기 규칙을 지난 일봉으로 돌려 보기 (AI 거부권은 과거 뉴스를 재현할 수 없어 빼고, 숫자 규칙만).

가정 (일봉이라 하루 안의 순서는 모른다 → 불리한 쪽으로):
- 신호는 그날 종가로 보고 종가에 산다 (실제로는 장중 2분마다 보고 그 가격에 산다)
- 다음 날부터: 저가가 손절가 이하면 손절가에 판다 (시가가 이미 아래면 시가). 같은 날 +1R 과 손절이 둘 다 닿으면 손절이 먼저
- +1R 에 닿으면 절반을 +1R 에 팔고 손절을 본전으로, 그 뒤 손절 = 최고가 - 1.5R (그날 고가로 올린 손절은 다음 날부터)
- 절반 뒤 +6R 에 닿으면 나머지를 판다. 10거래일 안에 +1R 에 못 닿으면 그날 종가에 정리
- 비용: 국내 수수료·슬리피지 편도 0.065% + 매도세 0.2%, 미국 0.3% (backtest.Costs)
- 한 종목은 한 번에 하나만. 거래마다 계좌의 한 종목 최대 비중(기본 20%)만큼 샀다고 보고 수익을 더한다
결과는 '이 규칙이 지난 시세에서 어땠는지'일 뿐 앞으로를 약속하지 않는다.
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind
from .backtest import Costs
from .models import Market
from .signals import rule

TIME_DAYS = 10


def simulate(bars: pd.DataFrame, market: Market, symbol: str = "") -> list[dict]:
    """한 종목의 거래 목록: [{symbol, kind, entry_date, exit_date, entry, ret, r, days, how}]."""
    c = Costs.default(market)
    cost_in, cost_out = c.fee + c.slippage, c.fee + c.slippage + c.sell_tax
    trades, i, n = [], 200, len(bars)
    idx = bars.index
    cl_, h_, lo_, v_ = bars["close"], bars["high"], bars["low"], bars["volume"]
    # 지표는 한 번만 계산하고 'i 일 아침 = 어제까지'로 하루 밀어 쓴다 (signals.check 와 같은 숫자)
    s20, s60, s200 = (ind.sma(cl_, k).shift(1) for k in (20, 60, 200))
    atr = ind.atr(h_, lo_, cl_).shift(1)
    rsi = ind.rsi(cl_)  # 오늘 종가를 넣은 RSI
    low3, yhigh, high20 = lo_.rolling(3).min().shift(1), h_.shift(1), h_.rolling(20).max().shift(1)
    avg20 = v_.rolling(20).mean().shift(1)
    while i < n - 1:
        day = str(idx[i])[:10]
        sig = rule(float(cl_.iloc[i]), float(s20.iloc[i]), float(s60.iloc[i]), float(s200.iloc[i]), float(atr.iloc[i]),
                   float(rsi.iloc[i]), float(low3.iloc[i]), float(yhigh.iloc[i]), float(high20.iloc[i]),
                   float(v_.iloc[i]), float(avg20.iloc[i]), 1.0)
        if not sig:
            i += 1
            continue
        entry = float(bars["close"].iloc[i])
        R = entry * sig["stop_pct"] / 100
        stop, high, half, parts, j = entry - R, entry, False, [], i + 1
        how = ""
        while j < n:
            o, h, lo, cl = (float(bars[k].iloc[j]) for k in ("open", "high", "low", "close"))
            if lo <= stop:  # 손절 (본전·따라 올린 손절 포함). 시가가 이미 아래면 시가에
                parts.append((0.5 if half else 1.0, min(o, stop)))
                how = "본전·따라 올린 손절" if half else "손절"
                break
            if not half and h >= entry + R:
                parts.append((0.5, entry + R))
                half, stop = True, max(stop, entry)
            if half and h >= entry + 6 * R:
                parts.append((0.5, entry + 6 * R))
                how = "+6R 익절"
                break
            if not half and j - i >= TIME_DAYS:
                parts.append((1.0, cl))
                how = "기간 정리"
                break
            high = max(high, h)
            if half:
                stop = max(stop, high - 1.5 * R)
            j += 1
        if not how:  # 기간 끝까지 들고 있음: 마지막 종가로 평가
            parts.append((0.5 if half else 1.0, float(bars["close"].iloc[-1])))
            how, j = "보유 중 (마지막 종가로 평가)", n - 1
        ret = sum(w * (p * (1 - cost_out) / (entry * (1 + cost_in)) - 1) for w, p in parts)
        trades.append({"symbol": symbol, "kind": sig["name"], "entry_date": day, "exit_date": str(idx[min(j, n - 1)])[:10],
                       "entry": round(entry, 4), "ret": round(ret, 5), "r": round(ret * entry / R, 2) if R else 0.0,
                       "days": int(min(j, n - 1) - i), "how": how, "half": half})
        i = max(j, i + 1)  # 판 다음 날부터 다시 신호를 본다
    return trades


def summarize(trades: list[dict], weight: float = 0.2) -> dict:
    if not trades:
        return {"trades": 0}
    rets = pd.Series([t["ret"] for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    seq = pd.Series([t["ret"] for t in sorted(trades, key=lambda t: t["exit_date"])])
    curve = 1 + (seq * weight).cumsum()  # 거래마다 계좌의 weight 만큼 (겹치는 거래도 각각)
    dd = float((curve / curve.cummax() - 1).min())
    return {"trades": len(trades), "win_rate": round(float((rets > 0).mean()), 4), "avg": round(float(rets.mean()), 5),
            "avg_win": round(float(wins.mean()), 5) if len(wins) else 0.0, "avg_loss": round(float(losses.mean()), 5) if len(losses) else 0.0,
            "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if len(losses) and losses.sum() < 0 else None,
            "avg_r": round(float(pd.Series([t["r"] for t in trades]).mean()), 2), "max_drawdown": round(dd, 4),
            "avg_days": round(float(pd.Series([t["days"] for t in trades]).mean()), 1),
            "half_rate": round(sum(t["half"] for t in trades) / len(trades), 4),
            "total": round(float(curve.iloc[-1] - 1), 4)}


def run(data, market: Market, symbols: list[str], days: int = 750, weight: float = 0.2) -> dict:
    trades, skipped = [], []
    for s in symbols:
        try:
            b = data.bars(market, s, days)
            if len(b) < 260:
                skipped.append(s)
                continue
            trades += simulate(b, market, s)
        except Exception:
            skipped.append(s)
    by_kind = {k: summarize([t for t in trades if t["kind"] == k], weight) for k in sorted({t["kind"] for t in trades})}
    seq = sorted(trades, key=lambda t: t["exit_date"])
    curve, eq = [], 0.0
    for t in seq:
        eq += t["ret"] * weight
        curve.append({"time": t["exit_date"], "value": round(eq, 4)})
    # 같은 날 여러 거래가 끝나면 마지막 값만 (차트는 날짜가 겹치면 안 된다)
    dedup = list({p["time"]: p for p in curve}.values())
    return {"summary": summarize(trades, weight), "by_kind": by_kind, "curve": dedup, "weight": weight, "symbols": len(symbols) - len(skipped),
            "skipped": skipped, "trades": sorted(trades, key=lambda t: t["entry_date"], reverse=True)[:40]}
