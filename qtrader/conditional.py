"""조건 주문: "이 가격에 닿으면 사기/팔기". 토스 실전은 토스 조건주문 API 를 쓰고, 가상매매는 여기서 2분마다 지켜 본다.

분할 매수·매도도 조건 주문으로 만든다: 첫 번째는 바로, 나머지는 가격이 정한 만큼 움직이면 나간다.
"""
from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

from .models import Market, OrderRequest, Side

KEY = "cond_orders"


def _load(store) -> list[dict]:
    return json.loads(store.get(KEY) or "[]")


def _save(store, rows: list[dict]) -> None:
    store.set(KEY, json.dumps(rows, ensure_ascii=False))


def market_of(sym: str) -> Market:
    return Market.KR if sym[:1].isdigit() else Market.US


def create(ctx, symbol: str, side: str, qty: int, trigger: float, price: float = 0, days: int = 30, note: str = "") -> dict:
    """price=0 이면 시장가 (미국은 지정가만)."""
    sym = symbol.upper()
    m = market_of(sym)
    if side not in ("buy", "sell") or qty <= 0 or trigger <= 0:
        raise ValueError("구분·수량·감시 가격을 확인하세요")
    if m == Market.US and not price:
        price = round(trigger, 2)
    expire = (date.today() + timedelta(days=max(1, min(days, 365)))).isoformat()
    row = {"id": uuid.uuid4().hex[:12], "symbol": sym, "market": m.value, "side": side, "qty": int(qty), "trigger": float(trigger),
           "price": float(price), "expire": expire, "status": "watching", "note": note, "broker": ctx.broker.name}
    if ctx.broker.name == "toss":
        from .engine import kr_tick
        body = {"symbol": sym.split(":")[-1], "type": "SINGLE", "quantity": str(int(qty)), "orderType": "LIMIT" if price else "MARKET",
                "clientOrderId": "qc" + row["id"], "expireDate": expire,
                "first": {"orderSide": side.upper(), "triggerPrice": str(kr_tick(trigger) if m == Market.KR else round(trigger, 2))}}
        if price:
            body["first"]["orderPrice"] = str(kr_tick(price) if m == Market.KR else round(price, 2))
        r = ctx.broker.client._call("POST", "/api/v1/conditional-orders", "ORDER", body=body, account=True) or {}
        row["toss_id"] = r.get("conditionalOrderId")
    rows = _load(ctx.store)
    rows.append(row)
    _save(ctx.store, rows)
    ctx.store.log("info", "manual", f"조건 주문: {sym} {trigger:,} 에 닿으면 {qty}주 {'매수' if side == 'buy' else '매도'}")
    return row


def cancel(ctx, cid: str) -> None:
    rows = _load(ctx.store)
    for r in rows:
        if r["id"] == cid and r["status"] == "watching":
            if r.get("toss_id") and ctx.broker.name == "toss":
                ctx.broker.client._call("DELETE", f"/api/v1/conditional-orders/{r['toss_id']}", "ORDER", account=True)
            r["status"] = "canceled"
    _save(ctx.store, rows)


def listing(ctx) -> list[dict]:
    rows = _load(ctx.store)
    if ctx.broker.name == "toss":  # 토스 쪽 상태 반영
        for r in rows:
            if r.get("toss_id") and r["status"] == "watching":
                try:
                    d = ctx.broker.client._call("GET", f"/api/v1/conditional-orders/{r['toss_id']}", "ORDER_HISTORY", account=True) or {}
                    st = d.get("status", "")
                    r["status"] = {"COMPLETED": "done", "ORDERED": "done", "EXPIRED": "expired", "CANCELED": "canceled"}.get(st, "watching")
                except Exception:
                    pass
        _save(ctx.store, rows)
    return [r for r in rows if r.get("broker") == ctx.broker.name][-50:]


def check(ctx) -> list[dict]:
    """가상매매·KIS 용: 가격이 닿은 조건 주문을 낸다. (토스는 토스가 직접 감시)"""
    if ctx.broker.name == "toss":
        return []
    rows, fired, today = _load(ctx.store), [], date.today().isoformat()
    for r in rows:
        if r["status"] != "watching" or r.get("broker") != ctx.broker.name:
            continue
        if r["expire"] < today:
            r["status"] = "expired"
            continue
        m = Market(r["market"])
        try:
            px = float(ctx.data.price(m, r["symbol"]))
        except Exception:
            continue
        hit = px <= r["trigger"] if r["side"] == "buy" else px >= r["trigger"]
        if not hit:
            continue
        req = OrderRequest(m, r["symbol"], Side(r["side"]), r["qty"], r["price"], "manual", f"조건 주문 ({r['trigger']:,} 도달)")
        res = ctx.broker.order(req)
        from .engine import trade_date
        ctx.store.add_order(trade_date=trade_date(m), account=ctx.broker.name, market=m.value, symbol=r["symbol"], side=r["side"],
                            qty=r["qty"], price=res.filled_price or px, status=("filled" if res.filled_qty else "submitted") if res.ok else "rejected",
                            broker_order_id=res.broker_order_id, strategy="manual", message=f"조건 주문 ({r['trigger']:,} 도달) | {res.message}")
        r["status"] = "done" if res.ok else "failed"
        r["result"] = res.message
        fired.append(r)
    _save(ctx.store, rows)
    return fired
