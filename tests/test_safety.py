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


def test_holding_modes_tighten_and_time_stop(ctx):
    from qtrader import principles
    f = principles.adjust_for_holding({"stop_loss_pct": 8, "take_profit_pct": 25, "horizon_days": 20}, "short")
    assert f["stop_loss_pct"] == 2.5 and f["take_profit_pct"] == 5 and f["horizon_days"] == 3
    assert principles.adjust_for_holding({"stop_loss_pct": 8}, "long") == {"stop_loss_pct": 8}
    ctx.cfg["ai"]["holding"] = "short"
    ctx.broker.positions["005930"] = Position(Market.KR, "005930", 5, 100.0, 101.0)
    ctx.store.save_plan("toss", "KR", "005930", "r", 100, 97, 105)
    assert check_exits(ctx, Market.KR) == []  # 아직 기한 전
    old = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat(timespec="seconds")
    ctx.store.exec("update plans set created_at=?", (old,))
    assert check_exits(ctx, Market.KR)[0]["reason"] == "time"


def test_partial_fill_protects_filled_part_then_settles(ctx):
    ctx.store.exec("insert into ai_runs (id, symbol, market, trade_date, started_at, status, final, reports) values "
                   "('r1','005930','KR',?,'x','done','{}', ?)",
                   (trade_date(Market.KR), '{"agents": {}, "plan": {"entry": 70000, "stop": 65000, "take": 80000}}'))
    oid = add(ctx, broker_order_id="P1")
    ctx.broker.client.details["P1"] = {"status": "PARTIALLY_FILLED", "execution": {"filledQuantity": "4", "averageFilledPrice": "70100"}}
    assert sync_orders(ctx) == []
    assert ctx.store.active_plans("toss")[0]["stop"] == 65000  # 체결된 4주는 바로 손절 보호
    assert "부분 체결 4/10" in ctx.store.rows("select message from orders where id=?", (oid,))[0]["message"]
    ctx.broker.client.details["P1"] = {"status": "CANCELED", "execution": {"filledQuantity": "4", "averageFilledPrice": "70100"}}
    out = sync_orders(ctx)
    row = ctx.store.rows("select * from orders where id=?", (oid,))[0]
    assert out[0]["status"] == "filled" and row["qty"] == 4 and row["price"] == 70100 and row["status"] == "filled"


def test_manual_buy_plan_after_toss_fill(ctx):
    import json
    oid = add(ctx, broker_order_id="M1", strategy="manual")
    ctx.store.set(f"manual_plan_{oid}", json.dumps({"stop": 66000, "take": 77000}))
    ctx.broker.client.details["M1"] = {"status": "FILLED", "execution": {"filledQuantity": "10", "averageFilledPrice": "70000"}}
    sync_orders(ctx)
    p = ctx.store.active_plans("toss")[0]
    assert (p["stop"], p["take"], p["run_id"]) == (66000, 77000, "manual")


def test_humanize_messages():
    from qtrader.humanize import say
    n = {"105560": "KB금융", "005930": "삼성전자"}
    f = lambda s: n.get(s, s)
    assert say("dry_run buy 105560 x8 @ 232,710.00 모의", f) == "KB금융 8주 매수 — 미리보기라 계산만 했어요 (주문 안 함)"
    assert say("오늘 AI 회의 후보: 005930", f) == "오늘 AI 회의 후보: 삼성전자"
    assert "긴급 정지" in say("킬 스위치 켬", f)


def test_heartbeat_gap_notice(ctx, monkeypatch):
    from qtrader import scheduler
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: True)
    old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="seconds")
    ctx.store.set("heartbeat", old)
    s = scheduler.Scheduler()
    s._last_exit = 999.0
    s._heartbeat(ctx, ctx.cfg, True)
    assert s._last_exit == 0.0
    assert any("꺼져 있었어요" in r["message"] for r in ctx.store.rows("select * from events where level='notice'"))


def test_planned_stop_does_not_warn(ctx, monkeypatch):
    """업데이트·직접 끄기로 꺼져 있었으면 '꺼져 있었어요' 경고 대신 안내만 (업데이트로 10분 넘게 걸려 놀라던 문제).
    표시는 켜진 뒤 첫 확인에서만 쓰고 지워서, 나중에 진짜로 꺼졌을 때는 그대로 경고한다."""
    from qtrader import scheduler
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: True)
    warns = lambda: [r["message"] for r in ctx.store.rows("select * from events where level='notice'") if "꺼져 있었어요" in r["message"]]
    old = (datetime.now(timezone.utc) - timedelta(minutes=25))
    ctx.store.set("heartbeat", old.isoformat(timespec="seconds"))
    scheduler.mark_planned_stop(ctx.store, "update", at=(old + timedelta(seconds=10)).isoformat(timespec="seconds"))
    s = scheduler.Scheduler()
    s._last_exit = 999.0
    s._heartbeat(ctx, ctx.cfg, True)
    assert warns() == [] and s._last_exit == 0.0
    assert any("업데이트를 마치고" in r["message"] for r in ctx.store.rows("select * from events"))
    assert ctx.store.get("planned_stop") is None
    ctx.store.set("heartbeat", old.isoformat(timespec="seconds"))  # 다음에 진짜로 꺼지면 경고
    s._heartbeat(ctx, ctx.cfg, True)
    assert len(warns()) == 1
    ctx.store.set("heartbeat", old.isoformat(timespec="seconds"))  # 예전에 적힌 끄기 표시는 이번 꺼짐을 가리지 않는다
    scheduler.mark_planned_stop(ctx.store, "quit", at=(old - timedelta(hours=3)).isoformat(timespec="seconds"))
    s._heartbeat(ctx, ctx.cfg, True)
    assert len(warns()) == 2


def test_extended_sessions_and_tick(ctx):
    from zoneinfo import ZoneInfo
    from qtrader.engine import kr_tick
    from qtrader.scheduler import trading_now
    kst = ZoneInfo("Asia/Seoul")
    eve = datetime(2026, 10, 28, 19, 0, tzinfo=kst)  # 수요일 저녁 7시
    assert ctx.cfg["markets"]["KR"]["extended"] and ctx.cfg["markets"]["US"]["extended_sessions"] == ["day", "pre", "after"]  # 기본: 모든 세션
    ctx.cfg["markets"]["KR"]["extended"] = False
    ctx.cfg["markets"]["US"]["extended_sessions"] = []
    assert not trading_now(ctx.cfg, Market.KR, eve)
    ctx.cfg["markets"]["KR"]["extended"] = True
    assert trading_now(ctx.cfg, Market.KR, eve) and not trading_now(ctx.cfg, Market.KR, eve.replace(hour=21))
    noon = datetime(2026, 10, 28, 12, 0, tzinfo=kst)
    assert not trading_now(ctx.cfg, Market.US, noon)
    ctx.cfg["markets"]["US"]["extended_sessions"] = ["day"]
    assert trading_now(ctx.cfg, Market.US, noon)
    assert kr_tick(70_345) == 70_300 and kr_tick(1_234) == 1_234 and kr_tick(250_700) == 250_500


def test_us_buy_skipped_when_dollars_short(ctx, monkeypatch):
    from qtrader import engine
    ctx.broker.client.buying_power = lambda cur: 100.0
    orders = [engine.OrderRequest(Market.US, "AAPL", engine.Side.BUY, 5, 200.0, "ai", "r")]
    monkeypatch.setattr(engine, "plan_orders", lambda *a, **k: orders)
    monkeypatch.setattr(engine, "panel", lambda *a, **k: {"AAPL": __import__("pandas").DataFrame({"close": [200.0] * 300})})
    ctx.cfg["markets"]["US"].update(enabled=True, strategy="trend")
    r = engine.run_market(ctx, Market.US, force=True)
    assert r["orders"][0]["status"] == "skipped_fx" and not ctx.broker.sent
    assert any("환전 우대" in e["message"] for e in ctx.store.rows("select * from events where level='notice'"))
