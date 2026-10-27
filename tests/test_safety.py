"""실거래 안전장치: 토스 체결 확인, 장중 손실 한도, 장 마감 자산 기록, 미체결 손절 재주문, 토큰 충돌 안내."""
from datetime import datetime, timedelta, timezone

import pytest

from qtrader import config
from qtrader.engine import Context, check_exits, check_loss, snapshot_equity, sync_orders, trade_date
from qtrader.models import Account, Market, OrderResult, Position
from qtrader.storage import Store


class FakeClient:
    def __init__(self):
        self.details, self.cancelled = {}, []

    def order_detail(self, oid):
        return self.details.get(oid, {"status": "PENDING"})

    def cancel(self, oid):
        self.cancelled.append(oid)
        return {"orderId": oid}


class FakeTossBroker:
    name, live = "toss", True

    def __init__(self, positions=None, cash=1_000_000):
        self.client = FakeClient()
        self.positions, self.cash, self.sent = positions or {}, cash, []

    def account(self, market):
        return Account(market, self.cash, {k: v for k, v in self.positions.items() if v.market == market})

    def order(self, req):
        self.sent.append(req)
        return OrderResult(True, f"OID{len(self.sent)}", "접수")


class Data:
    name = "fake"

    def price(self, m, s):
        return 100.0


@pytest.fixture
def ctx(tmp_path):
    cfg = config.load()
    return Context(cfg, Store(tmp_path / "s.db"), Data(), FakeTossBroker())


def add(ctx, **kw):
    base = dict(trade_date=trade_date(Market.KR), account="toss", market="KR", symbol="005930", side="buy", qty=10,
                price=70000, status="submitted", broker_order_id="A1", strategy="ai", message="m")
    return ctx.store.add_order(**(base | kw))


def test_sync_creates_plan_only_when_filled(ctx):
    oid = add(ctx)
    ctx.store.exec("insert into ai_runs (id, symbol, market, trade_date, started_at, status, final, reports) values "
                   "('r1','005930','KR',?,'x','done','{}', ?)",
                   (trade_date(Market.KR), '{"agents": {}, "plan": {"entry": 70000, "stop": 65000, "take": 80000}}'))
    assert sync_orders(ctx) == [] and not ctx.store.active_plans("toss")  # 아직 대기 -> 계획 없음
    ctx.broker.client.details["A1"] = {"status": "FILLED", "execution": {"filledQuantity": "10", "averageFilledPrice": "70000"}}
    out = sync_orders(ctx)
    assert out[0]["status"] == "filled"
    row = ctx.store.rows("select * from orders where id=?", (oid,))[0]
    assert row["status"] == "filled" and "체결 10주" in row["message"]
    assert ctx.store.active_plans("toss")[0]["stop"] == 65000


def test_sync_marks_cancelled(ctx):
    add(ctx, broker_order_id="B1")
    ctx.broker.client.details["B1"] = {"status": "CANCELED"}
    sync_orders(ctx)
    assert ctx.store.rows("select status from orders")[0]["status"] == "rejected"
    assert not ctx.store.active_plans("toss")


def test_intraday_loss_turns_on_kill_switch(ctx):
    yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    ctx.store.add_equity(yday, "toss", "KR", 1_000_000, 1_000_000)
    ctx.broker.cash = 990_000
    assert not check_loss(ctx, Market.KR)
    ctx.broker.cash = 900_000  # -10%
    assert check_loss(ctx, Market.KR) and ctx.store.get("kill_switch") == "1"


def test_snapshot_once_per_day(ctx):
    assert snapshot_equity(ctx, Market.KR) and not snapshot_equity(ctx, Market.KR)
    assert len(ctx.store.rows("select * from equity")) == 1


def test_us_stop_retry_after_three_minutes(ctx):
    ctx.broker.positions["AAPL"] = Position(Market.US, "AAPL", 5, 200.0, 150.0)
    ctx.store.save_plan("toss", "US", "AAPL", "r", 200, 180, 240)
    first = check_exits(ctx, Market.US)
    assert first[0]["reason"] == "stop" and ctx.broker.sent[-1].price == pytest.approx(149.55)
    assert check_exits(ctx, Market.US) == [] and len(ctx.broker.sent) == 1  # 기다리는 중엔 또 내지 않는다
    old = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(timespec="seconds")
    ctx.store.exec("update orders set ts=?", (old,))
    check_exits(ctx, Market.US)
    assert ctx.broker.client.cancelled == ["OID1"] and ctx.broker.sent[-1].price == pytest.approx(148.05)


def test_token_conflict_message(tmp_path):
    from qtrader.toss import Toss, TossError

    class R:
        def __init__(self, code, body):
            self.status_code, self.body, self.headers, self.text = code, body, {}, ""

        def json(self):
            return self.body

    class S:
        def post(self, *a, **k):
            return R(200, {"access_token": "T", "expires_in": 86400})

        def request(self, *a, **k):
            return R(401, {"error": {"code": "unauthorized", "message": ""}})

    t = Toss("tsck_live_x12345678", "s" * 16, tmp_path, session=S())
    with pytest.raises(TossError, match="다른 프로그램"):
        t.prices(["005930"])


def test_live_recheck_sells_only_on_trigger_and_caps(ctx):
    from qtrader import live
    from qtrader.agents.llm import Reply

    class LLM:
        calls = 0

        def run(self, *a, **k):
            LLM.calls += 1
            return Reply("", data={"action": "sell", "reason": "실적 쇼크 확정", "watch": "-"}, model="claude-sonnet-5-5")

    class Bars:
        def bars(self, m, s, n):
            import pandas as pd
            return pd.DataFrame({"close": [100.0, 100.0]})

        price = lambda self, m, s: 95.0

    ctx.data = Bars()
    ctx.broker.positions["005930"] = Position(Market.KR, "005930", 10, 100.0, 95.0)  # 오늘 -5%
    ctx.store.save_plan("toss", "KR", "005930", "r", 100, 90, 120)
    out = live.run_rechecks(ctx, Market.KR, llm=LLM())
    assert out[0]["action"] == "sell" and ctx.broker.sent[-1].side.value == "sell"
    assert live.run_rechecks(ctx, Market.KR, llm=LLM()) == [] and LLM.calls == 1  # 같은 종목은 하루 한 번
