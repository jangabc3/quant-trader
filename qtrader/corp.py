"""기업 이벤트: 액면분할·병합·무상증자·유상증자·감자·배당.

토스 API 에는 기업 이벤트 정보가 없어서 두 가지로 알아챈다.
1. 계좌 변화: 우리가 사고판 주문이 없는데 보유 수량이 바뀌고, 평균 단가가 같은 비율로 반대로 바뀌면 분할·병합·무상증자로 본다.
   -> 손절·익절·매수가를 같은 비율로 고친다 (안 고치면 1:5 분할 뒤 가격이 손절가 아래로 '떨어진 것처럼' 보여 잘못 판다).
2. DART 공시 (국내, 키가 있을 때 하루 한 번): 보유 종목에 분할·병합·증자·감자 공시가 나오면 알리고,
   토스 서버 보호(OCO)를 잠시 풀어 옛 가격으로 잘못 발동되지 않게 한다. 수량 변화가 반영되면 다시 건다.

안전장치: 국내는 하루 가격 제한이 ±30% 라서, 한 번에 31% 넘게 떨어졌다면 거의 확실히 기업 이벤트다.
그런 경우 자동 손절을 멈추고 확인을 기다린다 (blocked_gap).
"""
from __future__ import annotations

import json
from datetime import date, datetime

from .models import Market

SNAP = "pos_snap"      # {"KR:005930": {"qty", "avg", "px", "ts"}}
EVENTS = "corp_events"  # {"KR:005930": {"title", "date", "kind"}}
HOLD = "corp_hold"      # {"KR:005930": "2026-10-27"}  토스 보호를 잠시 풀어 둔 종목

SPLIT_WORDS = ("주식분할", "주식병합", "무상증자", "감자", "액면분할", "액면병합")
INFO_WORDS = ("유상증자", "현금ㆍ현물배당", "현금배당", "합병", "회사분할", "분할합병")


def _j(store, k):
    return json.loads(store.get(k) or "{}")


def _put(store, k, v):
    store.set(k, json.dumps(v, ensure_ascii=False))


def held(store, market: Market, symbol: str) -> bool:
    """DART 이벤트 때문에 토스 보호를 잠시 풀어 둔 종목인가."""
    h = _j(store, HOLD).get(f"{market.value}:{symbol}")
    return bool(h) and (date.today() - date.fromisoformat(h)).days <= 30


def event_of(store, market: str, symbol: str) -> dict | None:
    return _j(store, EVENTS).get(f"{market}:{symbol}")


def _traded_since(ctx, market: Market, symbol: str, ts: str) -> bool:
    return bool(ctx.store.rows("select 1 from orders where account=? and market=? and symbol=? and ts>=? "
                               "and status in ('filled','submitted') limit 1", (ctx.broker.name, market.value, symbol, ts)))


def _notify(ctx, msg):
    ctx.store.log("warn", "corp", msg)
    try:
        from .extras import notify
        notify(ctx.store, ctx.cfg, msg, "corp")
    except Exception:
        pass


def check(ctx) -> list[dict]:
    """수량·평단 변화로 분할·병합을 알아채 계획을 고친다. 바뀐 것을 돌려준다."""
    snap, out = _j(ctx.store, SNAP), []
    now = datetime.now().isoformat(timespec="seconds")
    for m in (Market.KR, Market.US):
        if not ctx.cfg["markets"][m.value].get("enabled"):
            continue
        try:
            pos = ctx.broker.account(m).positions
        except Exception:
            continue
        plans = {p["symbol"]: p for p in ctx.store.active_plans(ctx.broker.name, m.value)}
        for sym, ps in pos.items():
            k = f"{m.value}:{sym}"
            prev = snap.get(k)
            if prev and ps.qty != prev["qty"] and prev["qty"] > 0 and not _traded_since(ctx, m, sym, prev["ts"]):
                r = ps.qty / prev["qty"]
                avg_r = (prev["avg"] / ps.avg_price) if ps.avg_price else 0
                if avg_r and abs(avg_r - r) / r < 0.05:
                    kind = "분할·무상증자" if r > 1 else "병합·감자"
                    pl = plans.get(sym)
                    if pl:
                        ctx.store.save_plan(ctx.broker.name, m.value, sym, pl.get("run_id") or "corp", pl["entry"] / r,
                                            pl["stop"] / r, pl["take"] / r, pl.get("horizon"), pl.get("style"),
                                            (pl.get("risk") or 0) / r or None, (pl.get("high") or 0) / r or None,
                                            pl.get("half") or 0, pl.get("created_at"))
                    hold = _j(ctx.store, HOLD)
                    hold.pop(k, None)
                    _put(ctx.store, HOLD, hold)
                    _notify(ctx, f"🔁 {sym} {kind} 반영: {prev['qty']}주 → {ps.qty}주 (1:{r:g}). 손절·익절 가격도 같은 비율로 고쳤어요.")
                    out.append({"symbol": sym, "ratio": r, "kind": kind})
                else:
                    _notify(ctx, f"❓ {sym} 주문 없이 보유 수량이 {prev['qty']}주 → {ps.qty}주로 바뀌었어요. 토스 앱에서 확인해 주세요 (유상증자·입고 등).")
            prev = snap.get(k) or {}
            # ref = 지난 거래일 마지막 가격 (오늘 안에서는 바꾸지 않는다). 분할이 반영되면 같은 비율로 고친다
            ref = prev.get("px") if prev.get("ts", "")[:10] != now[:10] else prev.get("ref")
            if out and out[-1]["symbol"] == sym and ref:
                ref = ref / out[-1]["ratio"]
            snap[k] = {"qty": ps.qty, "avg": ps.avg_price, "px": ps.last_price or prev.get("px", 0), "ref": ref, "ts": now}
        for k in [k for k in snap if k.startswith(m.value + ":") and k.split(":", 1)[1] not in pos]:
            snap.pop(k)
    _put(ctx.store, SNAP, snap)
    return out


def blocked_gap(ctx, market: Market, symbol: str, px: float) -> bool:
    """국내에서 직전 기록보다 31% 넘게 낮으면 (가격 제한 밖) 분할·병합일 가능성이 매우 높다 -> 자동 손절을 멈춘다."""
    if market != Market.KR:
        return False
    prev = _j(ctx.store, SNAP).get(f"{market.value}:{symbol}") or {}
    ref = prev.get("ref") if prev.get("ts", "")[:10] == date.today().isoformat() else prev.get("px")
    if not ref or px >= ref * 0.69:
        return False
    key = f"gapwarn_{symbol}_{date.today().isoformat()}"
    if not ctx.store.get(key):
        ctx.store.set(key, "1")
        _notify(ctx, f"⚠️ {symbol} 가격이 {ref:,.0f} → {px:,.0f} 로 한 번에 31% 넘게 바뀌었어요. 국내 가격 제한(±30%) 밖이라 "
                     f"액면분할·병합일 가능성이 커서 자동 손절을 멈췄어요. 수량이 반영되면 손절·익절을 같은 비율로 고쳐 다시 지켜 봐요.")
    return True


def dart_scan(ctx) -> list[dict]:
    """하루 한 번: 보유 국내 종목의 최근 공시에서 기업 이벤트를 찾는다."""
    today = date.today().isoformat()
    if ctx.store.get("corp_scan") == today:
        return []
    ctx.store.set("corp_scan", today)
    from . import config as config_mod
    from .screener import disclosure_titles
    ev, hold, out = _j(ctx.store, EVENTS), _j(ctx.store, HOLD), []
    try:
        pos = ctx.broker.account(Market.KR).positions
    except Exception:
        return []
    for sym in pos:
        titles = disclosure_titles(sym, config_mod.home(), 10)
        hit = next((t for t in titles if any(w in t for w in SPLIT_WORDS)), None)
        info = next((t for t in titles if any(w in t for w in INFO_WORDS)), None)
        t = hit or info
        k = f"KR:{sym}"
        if not t or (ev.get(k) or {}).get("title") == t:
            continue
        ev[k] = {"title": t, "date": today, "kind": "split" if hit else "info"}
        if hit:
            hold[k] = today
            try:
                from .guard import release
                release(ctx, Market.KR, sym)
            except Exception:
                pass
            _notify(ctx, f"📢 {sym} 공시: {t}. 분할·병합이 반영될 때 옛 가격으로 잘못 팔리지 않게 토스 서버 보호를 잠시 풀고 "
                         f"QTrader 가 지켜 봐요. 수량이 바뀌면 손절·익절을 같은 비율로 고쳐요.")
        else:
            _notify(ctx, f"📢 {sym} 공시: {t}. 주가에 영향을 줄 수 있어요 (배당락·권리락 날 가격이 내려갈 수 있음).")
        out.append({"symbol": sym, "title": t})
    _put(ctx.store, EVENTS, {k: v for k, v in ev.items() if (date.today() - date.fromisoformat(v["date"])).days <= 60})
    _put(ctx.store, HOLD, hold)
    return out
