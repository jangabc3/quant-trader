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


def create(ctx, symbol: str, side: str, qty: int, trigger: float, price: float = 0, days: int = 30, note: str = "",
           then_stop: float = 0) -> dict:
    """price=0 이면 시장가 (미국은 지정가만). then_stop>0 이면 OTO: 사지면 그 가격 아래로 내릴 때 파는 손절이 이어서 걸린다."""
    sym = symbol.upper()
    m = market_of(sym)
    if side not in ("buy", "sell") or qty <= 0 or trigger <= 0:
        raise ValueError("구분·수량·감시 가격을 확인하세요")
    if m == Market.US and not price:
        price = round(trigger, 2)
    expire = (date.today() + timedelta(days=max(1, min(days, 365)))).isoformat()
    if then_stop and (side != "buy" or then_stop >= trigger):
        raise ValueError("이어서 걸 손절가는 매수 감시가보다 낮아야 해요")
    try:
        cur = float(ctx.data.price(m, sym))
    except Exception:
        cur = trigger
    # 매수는 내려와서 닿을 때, 매도는 올라가서 닿을 때가 기본. 지금가보다 낮은 매도 감시가는 손절(내려가서 닿으면)로 본다
    direction = "down" if side == "buy" or trigger < cur else "up"
    row = {"id": uuid.uuid4().hex[:12], "symbol": sym, "market": m.value, "side": side, "qty": int(qty), "trigger": float(trigger),
           "price": float(price), "expire": expire, "status": "watching", "note": note, "broker": ctx.broker.name, "dir": direction,
           "then_stop": float(then_stop or 0)}
    if then_stop and not price:
        price = row["price"] = float(trigger)  # OTO 는 지정가만
    if ctx.broker.name == "toss":
        from .engine import kr_tick
        fmt = lambda v: str(kr_tick(v) if m == Market.KR else round(v, 2))
        body = {"symbol": sym.split(":")[-1], "type": "OTO" if then_stop else "SINGLE", "quantity": str(int(qty)),
                "orderType": "LIMIT" if price else "MARKET", "clientOrderId": "qc" + row["id"], "expireDate": expire,
                "first": {"orderSide": side.upper(), "triggerPrice": fmt(trigger)}}
        if price:
            body["first"]["orderPrice"] = fmt(price)
        if then_stop:
            body["second"] = {"orderSide": "SELL", "triggerPrice": fmt(then_stop), "orderPrice": fmt(then_stop * 0.99)}
        r = ctx.broker.client._call("POST", "/api/v1/conditional-orders", "CONDITIONAL_ORDER", body=body, account=True) or {}
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
                ctx.broker.client._call("DELETE", f"/api/v1/conditional-orders/{r['toss_id']}", "CONDITIONAL_ORDER", account=True)
            r["status"] = "canceled"
    _save(ctx.store, rows)


def listing(ctx) -> list[dict]:
    """우리가 건 조건 주문 + (토스면) 토스 앱 등 다른 곳에서 건 조건 주문까지."""
    rows = _load(ctx.store)
    out = [r for r in rows if r.get("broker") == ctx.broker.name][-50:]
    if ctx.broker.name == "toss":
        from .guard import _load as guards
        gids = {v["id"] for v in guards(ctx.store).values()}
        try:
            open_ = ctx.broker.client._call("GET", "/api/v1/conditional-orders", "CONDITIONAL_ORDER_HISTORY",
                                            {"status": "OPEN", "limit": 100}, account=True) or {}
            live = {x["conditionalOrderId"]: x for x in open_.get("conditionalOrders", [])}
        except Exception:
            live = None
        if live is not None:
            known = set()
            for r in out:
                if r.get("toss_id") and r["status"] == "watching":
                    known.add(r["toss_id"])
                    if r["toss_id"] not in live:
                        r["status"] = "done"  # 열려 있지 않으면 실행됐거나 끝남
            _save(ctx.store, rows)
            for cid, x in live.items():  # 토스 앱에서 건 것·QTrader 손절·익절 보호
                if cid in known:
                    continue
                f = x.get("first") or {}
                out.append({"id": "t:" + cid, "toss_id": cid, "symbol": x.get("symbol"), "market": x.get("market") or ("KR" if str(x.get("symbol", ""))[:1].isdigit() else "US"),
                            "side": (f.get("orderSide") or "").lower(), "qty": int(float(x.get("quantity") or 0)), "trigger": float(f.get("triggerPrice") or 0),
                            "price": float(f.get("orderPrice") or 0), "expire": x.get("expireDate") or "", "status": "watching",
                            "note": "QTrader 손절·익절 보호 (OCO)" if cid in gids else f"토스에서 건 {x.get('type', '')} 조건주문", "broker": "toss",
                            "external": cid not in gids, "guard": cid in gids})
    return out


def modify(ctx, cid: str, trigger: float, price: float = 0, qty: int = 0) -> dict:
    """감시가·주문가·수량 바꾸기. 토스는 수정하면 새 번호가 나온다."""
    rows = _load(ctx.store)
    r = next((x for x in rows if x["id"] == cid and x["status"] == "watching"), None)
    if not r:
        raise ValueError("지켜보는 중인 조건 주문만 바꿀 수 있어요")
    m = Market(r["market"])
    r["trigger"], r["qty"] = float(trigger), int(qty or r["qty"])
    r["price"] = float(price or (trigger if r["price"] else 0))
    if r.get("toss_id") and ctx.broker.name == "toss":
        from .engine import kr_tick
        fmt = lambda v: str(kr_tick(v) if m == Market.KR else round(v, 2))
        body = {"type": "OTO" if r.get("then_stop") else "SINGLE", "quantity": str(r["qty"]), "orderType": "LIMIT" if r["price"] else "MARKET",
                "expireDate": r["expire"], "first": {"orderSide": r["side"].upper(), "triggerPrice": fmt(r["trigger"])}}
        if r["price"]:
            body["first"]["orderPrice"] = fmt(r["price"])
        if r.get("then_stop"):
            body["second"] = {"orderSide": "SELL", "triggerPrice": fmt(r["then_stop"]), "orderPrice": fmt(r["then_stop"] * 0.99)}
        res = ctx.broker.client._call("POST", f"/api/v1/conditional-orders/{r['toss_id']}/modify", "CONDITIONAL_ORDER", body=body, account=True) or {}
        r["toss_id"] = res.get("conditionalOrderId") or r["toss_id"]
    _save(ctx.store, rows)
    ctx.store.log("info", "manual", f"조건 주문 수정: {r['symbol']} {trigger:,} · {r['qty']}주")
    return r


def check(ctx) -> list[dict]:
    """가상매매·KIS 용: 가격이 닿은 조건 주문을 낸다. (토스는 토스가 직접 감시)"""
    if ctx.broker.name == "toss":
        return []
    rows, fired, today = _load(ctx.store), [], date.today().isoformat()
    for r in list(rows):  # OTO 로 이어 붙는 손절은 다음 차례부터 지켜 본다
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
        hit = px <= r["trigger"] if r.get("dir", "down" if r["side"] == "buy" else "up") == "down" else px >= r["trigger"]
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
        if res.ok and r.get("then_stop"):  # OTO: 사지면 이어서 손절 감시
            rows.append({"id": uuid.uuid4().hex[:12], "symbol": r["symbol"], "market": r["market"], "side": "sell", "qty": r["qty"],
                         "trigger": r["then_stop"], "price": 0 if m == Market.KR else r["then_stop"], "expire": r["expire"], "status": "watching",
                         "note": "이어서 건 손절 (OTO)", "broker": r["broker"], "dir": "down", "then_stop": 0})
    _save(ctx.store, rows)
    return fired
