"""브로커: 가상매매(paper)와 한국투자증권(KIS). 같은 인터페이스를 쓴다."""
from __future__ import annotations

from typing import Callable, Protocol

from .backtest import Costs
from .kis import KIS
from .models import Account, Market, OrderRequest, OrderResult, Position, Side
from .storage import Store


class Broker(Protocol):
    name: str
    live: bool

    def account(self, market: Market) -> Account: ...

    def order(self, req: OrderRequest) -> OrderResult: ...


class PaperBroker:
    """SQLite 에 잔고를 두고 요청 가격에 바로 체결한다. 수수료·세금·슬리피지는 백테스트와 같은 값."""

    live = False

    def __init__(self, store: Store, initial_cash: dict[str, float], price: Callable[[Market, str], float],
                 account_id: str = "paper"):
        self.store, self.price, self.name = store, price, account_id
        for m, amt in initial_cash.items():
            store.exec("insert or ignore into cash values (?,?,?)", (account_id, m, float(amt)))

    def account(self, market: Market) -> Account:
        cash = self.store.rows("select amount from cash where account=? and market=?", (self.name, market.value))
        pos = {}
        for r in self.store.rows("select * from positions where account=? and market=? and qty>0",
                                 (self.name, market.value)):
            try:
                last = self.price(market, r["symbol"])
            except Exception:
                last = r["avg_price"]
            pos[r["symbol"]] = Position(market, r["symbol"], r["qty"], r["avg_price"], last)
        return Account(market, cash[0]["amount"] if cash else 0.0, pos)

    def order(self, req: OrderRequest) -> OrderResult:
        c = Costs.default(req.market)
        px = req.price if req.price > 0 else self.price(req.market, req.symbol)
        px = px * (1 + c.slippage) if req.side == Side.BUY else px * (1 - c.slippage)
        if req.price > 0:  # 지정가보다 불리하게 체결되지 않는다
            px = min(px, req.price) if req.side == Side.BUY else max(px, req.price)
        acct = self.account(req.market)
        gross = px * req.qty
        if req.side == Side.BUY:
            total = gross * (1 + c.fee)
            if total > acct.cash:
                return OrderResult(False, message="가상계좌 현금 부족")
            old = acct.positions.get(req.symbol)
            q0, a0 = (old.qty, old.avg_price) if old else (0, 0.0)
            q1 = q0 + req.qty
            avg = (q0 * a0 + req.qty * px) / q1
            cash = acct.cash - total
        else:
            old = acct.positions.get(req.symbol)
            if not old or old.qty < req.qty:
                return OrderResult(False, message="가상계좌 보유 수량 부족")
            q1, avg = old.qty - req.qty, old.avg_price
            cash = acct.cash + gross * (1 - c.fee - c.sell_tax)
        self.store.exec("insert into positions values (?,?,?,?,?) on conflict(account,market,symbol) "
                        "do update set qty=excluded.qty, avg_price=excluded.avg_price",
                        (self.name, req.market.value, req.symbol, q1, avg))
        self.store.exec("update cash set amount=? where account=? and market=?", (cash, self.name, req.market.value))
        return OrderResult(True, "paper", "가상 체결", req.qty, px)


class TossBroker:
    """토스증권 실전 계좌. 같은 날 같은 주문이 두 번 나가지 않게 clientOrderId(멱등성 키)를 붙인다."""

    live = True

    def __init__(self, client, us_loc: bool = True):
        self.client, self.us_loc = client, us_loc
        self.name = "toss"

    def account(self, market: Market) -> Account:
        return self.client.account(market)

    def order(self, req: OrderRequest) -> OrderResult:
        from datetime import date
        import uuid

        if req.strategy == "manual":  # 사용자가 직접 누른 주문: 같은 주문을 또 낼 수 있게 매번 새 번호, 일반 지정가
            return self.client.order(req, client_order_id="qm" + uuid.uuid4().hex[:30], loc=False)
        coid = f"qt{date.today():%y%m%d}{req.market.value}{req.side.value[0]}{req.qty}-{req.symbol.replace(':', '')}"
        return self.client.order(req, client_order_id=coid, loc=self.us_loc)


class KISBroker:
    def __init__(self, client: KIS):
        self.client = client
        self.live = client.env == "real"
        self.name = f"kis_{client.env}:{client.cano}-{client.prdt}"

    def account(self, market: Market) -> Account:
        return self.client.account(market)

    def order(self, req: OrderRequest) -> OrderResult:
        return self.client.order(req)
