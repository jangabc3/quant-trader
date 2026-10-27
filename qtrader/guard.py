"""토스 서버 손절·익절 보호 (OCO 조건주문).

손절·익절 계획이 있는 보유 종목마다 토스에 OCO 를 하나 걸어 둔다:
  first  = 익절 (감시가 > 현재가, 매도)   second = 손절 (감시가 < 현재가, 매도)
둘 중 먼저 닿는 쪽이 팔리고 나머지는 토스가 자동 취소한다. PC·프로그램이 꺼져 있어도 토스가 지켜 준다.

- 국내는 KRX 정규장에서만 발동된다 (토스 명세). 넥스트레이드 시간외는 QTrader 의 2분 감시가 맡는다.
- 미국은 거래 가능한 모든 시간대에 발동된다.
- sync() 를 몇 분마다 불러 '있어야 할 보호'와 '걸려 있는 보호'를 맞춘다 (새로 걸기·수량 바뀌면 다시 걸기·계획이 없어지면 취소).
- QTrader 가 직접 팔 때(기간 만료·AI 재점검·직접 매도)는 release() 로 먼저 풀어서 두 번 팔리지 않게 한다.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from .models import Market

KEY = "toss_guards"  # {"KR:005930": {"id", "qty", "stop", "take"}}


def enabled(ctx) -> bool:
    return ctx.broker.name == "toss" and (ctx.cfg.get("toss") or {}).get("server_guard", True)


def _load(store) -> dict:
    return json.loads(store.get(KEY) or "{}")


def _save(store, g: dict) -> None:
    store.set(KEY, json.dumps(g, ensure_ascii=False))


def active(ctx, market: Market, symbol: str) -> bool:
    return enabled(ctx) and f"{market.value}:{symbol}" in _load(ctx.store)


def _px(market: Market, v: float, down: bool = False) -> str:
    from .engine import kr_tick
    return str(kr_tick(v)) if market == Market.KR else f"{v:.2f}"


def _body(market: Market, symbol: str, qty: int, stop: float, take: float) -> dict:
    # 손절은 닿으면 조금 아래 지정가로 내서 체결되게, 익절은 감시가 그대로
    stop_order = stop * (0.99 if market == Market.KR else 0.995)
    return {"symbol": symbol.split(":")[-1], "type": "OCO", "quantity": str(int(qty)), "orderType": "LIMIT",
            "expireDate": (date.today() + timedelta(days=364)).isoformat(),
            "first": {"orderSide": "SELL", "triggerPrice": _px(market, take, False), "orderPrice": _px(market, take, False)},
            "second": {"orderSide": "SELL", "triggerPrice": _px(market, stop, True), "orderPrice": _px(market, stop_order, True)}}


def protect(ctx, market: Market, symbol: str, qty: int, stop: float, take: float) -> str | None:
    if not enabled(ctx):
        return None
    release(ctx, market, symbol)
    c = ctx.broker.client
    r = c._call("POST", "/api/v1/conditional-orders", "CONDITIONAL_ORDER", body=_body(market, symbol, qty, stop, take), account=True) or {}
    gid = r.get("conditionalOrderId")
    if gid:
        g = _load(ctx.store)
        g[f"{market.value}:{symbol}"] = {"id": gid, "qty": int(qty), "stop": stop, "take": take}
        _save(ctx.store, g)
        ctx.store.log("info", "guard", f"{symbol} 토스 서버 보호 걸기: 손절 {stop:,} · 익절 {take:,} ({qty}주)")
    return gid


def release(ctx, market: Market, symbol: str) -> None:
    if ctx.broker.name != "toss":
        return
    g = _load(ctx.store)
    k = f"{market.value}:{symbol}"
    if k not in g:
        return
    try:
        ctx.broker.client._call("DELETE", f"/api/v1/conditional-orders/{g[k]['id']}", "CONDITIONAL_ORDER", account=True)
    except Exception as e:  # 이미 실행·만료됐으면 취소가 실패해도 괜찮다
        ctx.store.log("info", "guard", f"{symbol} 보호 취소 응답: {e}")
    g.pop(k, None)
    _save(ctx.store, g)


def guarded(ctx) -> dict:
    """화면 표시용: {"KR:005930": {...}}"""
    return _load(ctx.store) if enabled(ctx) else {}


def sync(ctx) -> list[str]:
    """있어야 할 보호와 걸려 있는 보호를 맞춘다. 바뀐 것을 돌려준다."""
    if not enabled(ctx):
        return []
    changed = []
    g = _load(ctx.store)
    want = {}
    for m in (Market.KR, Market.US):
        if not ctx.cfg["markets"][m.value].get("enabled"):
            continue
        try:
            pos = ctx.broker.account(m).positions
        except Exception:
            continue
        for p in ctx.store.active_plans(ctx.broker.name, m.value):
            ps = pos.get(p["symbol"])
            k = f"{m.value}:{p['symbol']}"
            if k in g and not (ps and ps.qty > 0):  # 토스가 OCO 로 팔았다 (손절 또는 익절)
                ctx.store.close_plan(ctx.broker.name, m.value, p["symbol"], "toss_oco")
                g.pop(k, None)
                _save(ctx.store, g)
                msg = f"🛡 {p['symbol']} 토스 서버 손절·익절이 실행돼 모두 팔렸어요. 체결 가격은 토스 앱 주문내역에서 확인하세요."
                ctx.store.log("info", "guard", msg)
                try:
                    from .extras import notify
                    notify(ctx.store, ctx.cfg, msg, "guard")
                except Exception:
                    pass
                changed.append(p["symbol"])
                continue
            if ps and ps.qty > 0 and 0 < p["stop"] < (ps.last_price or p["entry"]) < p["take"]:
                want[f"{m.value}:{p['symbol']}"] = (m, p["symbol"], ps.qty, p["stop"], p["take"])
    for k, (m, s, q, st, tk) in want.items():
        cur = g.get(k)
        if not cur or cur["qty"] != q or abs(cur["stop"] - st) > 1e-6 or abs(cur["take"] - tk) > 1e-6:
            try:
                protect(ctx, m, s, q, st, tk)
                changed.append(s)
            except Exception as e:
                ctx.store.log("warn", "guard", f"{s} 토스 서버 보호 실패 (QTrader 가 대신 2분마다 지켜 봅니다): {e}")
    for k in list(_load(ctx.store)):
        if k not in want:
            m, s = k.split(":", 1)
            release(ctx, Market(m), s)
            changed.append(s)
    return changed
