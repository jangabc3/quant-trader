"""AI 회의 적중률 기록판. 결정 당시 가격과 그 뒤 가격을 비교해 실제로 맞았는지 센다.

승률 100% 는 어떤 방법으로도 보장할 수 없다. 대신 결정을 전부 기록하고 결과를 숨김없이 보여줘서
어떤 등급·종목에서 AI 판단이 믿을 만한지 사용자가 직접 판단하게 한다.
"""
from __future__ import annotations

import pandas as pd

from .context import market_of

BULLISH = {"Buy", "Overweight"}
BEARISH = {"Sell", "Underweight"}


def evaluate(runs: list[dict], bars_for) -> dict:
    """runs: store.ai_runs() 결과, bars_for(symbol) -> 일봉 DataFrame."""
    rows, cache = [], {}
    for r in runs:
        f = r.get("final") or {}
        rating = f.get("rating")
        if r.get("status") != "done" or not rating:
            continue
        s = r["symbol"]
        if s not in cache:
            try:
                cache[s] = bars_for(s)
            except Exception:
                cache[s] = None
        df = cache[s]
        if df is None or df.empty:
            continue
        d0 = pd.Timestamp(r["trade_date"])
        before = df[df.index <= d0]
        if before.empty:
            continue
        plan = r.get("plan") if isinstance(r.get("plan"), dict) else {}
        p0 = float(plan.get("price") or before["close"].iloc[-1])  # 회의하던 때 가격 (장중 회의는 그날 종가가 아니다)
        after = df[df.index > d0]
        last = float(df["close"].iloc[-1])
        horizon = int(f.get("horizon_days") or 20)
        ret = last / p0 - 1
        ret_h = float(after["close"].iloc[horizon - 1]) / p0 - 1 if len(after) >= horizon else None
        days = len(after)
        sl, tp = float(f.get("stop_loss_pct") or 0) / 100, float(f.get("take_profit_pct") or 0) / 100
        hit_sl = hit_tp = False
        if days and rating in BULLISH:
            hit_sl = sl > 0 and float(after["low"].min()) <= p0 * (1 - sl)
            hit_tp = tp > 0 and float(after["high"].max()) >= p0 * (1 + tp)
        if rating in BULLISH:
            ok = ret > 0
        elif rating in BEARISH:
            ok = ret < 0
        else:
            ok = None  # Hold 은 맞고 틀림을 세지 않는다
        rows.append({"id": r["id"], "symbol": s, "name": r.get("name", s), "market": market_of(s).value,
                     "date": r["trade_date"], "rating": rating, "confidence": f.get("confidence"), "headline": f.get("headline", ""),
                     "price": p0, "last": last, "return": round(ret, 4),
                     "return_h": round(ret_h, 4) if ret_h is not None else None, "days": days,
                     "hit_stop": hit_sl, "hit_take": hit_tp, "correct": ok, "matured": days >= horizon})
    judged = [x for x in rows if x["correct"] is not None and x["days"] > 0]
    by = {}
    for x in rows:
        b = by.setdefault(x["rating"], {"count": 0, "returns": []})
        b["count"] += 1
        if x["days"]:
            b["returns"].append(x["return"])
    summary = {
        "decisions": len(rows), "judged": len(judged),
        "hit_rate": round(sum(1 for x in judged if x["correct"]) / len(judged), 4) if judged else None,
        "avg_return_bullish": _avg([x["return"] for x in judged if x["rating"] in BULLISH]),
        "avg_return_bearish": _avg([x["return"] for x in judged if x["rating"] in BEARISH]),
        "by_rating": {k: {"count": v["count"], "avg_return": _avg(v["returns"])} for k, v in by.items()},
    }
    return {"summary": summary, "rows": rows}


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None
