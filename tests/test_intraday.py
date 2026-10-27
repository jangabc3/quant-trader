"""장중 계속 사기: 장중 회의 결론 -> 그 종목만 바로 매수 (리밸런싱 없음), 비용·안전 한도."""
import json

import pytest

from qtrader import config
from qtrader.agents import meeting
from qtrader.data import write_demo_csv
from qtrader.engine import build_context, buy_one, trade_date
from qtrader.models import Market, OrderRequest, Side
from qtrader.scheduler import Scheduler

from test_toss_ai import FakeLLM, loose_cfg


@pytest.fixture
def ctx(isolated_home, monkeypatch):
    monkeypatch.setattr(meeting, "_live", {})  # 다른 시험의 회의 기록과 섞이지 않게
    cfg = loose_cfg()
    cfg["ai"]["require_approval"] = False
    cfg["ai"]["holding"] = "long"  # 하루 신규 매수 한도를 규칙 그대로 (단기·자동은 +1~2)
    cfg["markets"]["KR"]["strategy"] = "ai"
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    c = build_context()
    c.store.set(f"picks_KR_{trade_date(Market.KR)}", json.dumps([{"symbol": "005930"}, {"symbol": "000660"}]))
    return c


@pytest.fixture
def fake_meetings(monkeypatch):
    """scheduler 가 여는 회의를 가짜 Claude 로 바로 끝낸다. 열린 회의 종목을 기록한다."""
    opened, real = [], meeting.start

    def start(symbol, data, store, cfg, llm=None, toss=None, background=True):
        opened.append(symbol)
        rating = "Sell" if symbol in start.sell else "Buy"
        return real(symbol, data, store, cfg, llm=FakeLLM(rating, 0.9), toss=toss, background=False)
    start.sell = set()
    monkeypatch.setattr(meeting, "start", start)
    return start, opened


def orders(ctx):
    return [(o["symbol"], o["side"], o["status"]) for o in ctx.store.rows("select * from orders order by id")]


def test_intraday_meeting_then_one_buy(ctx, fake_meetings):
    _, opened = fake_meetings
    s = Scheduler()
    s._intraday(ctx, Market.KR)  # 1) 회의 열기
    assert opened == ["005930"] and orders(ctx) == []
    st = json.loads(ctx.store.get(f"intraday_KR_{trade_date(Market.KR)}"))
    assert st["005930"]["state"] == "meeting"
    out = s._intraday(ctx, Market.KR)  # 2) 결론이 났으니 그 종목만 산다. 간격(15분) 전이라 새 회의는 없다
    assert [(o["symbol"], o["status"]) for o in out] == [("005930", "filled")]
    assert orders(ctx) == [("005930", "buy", "filled")] and opened == ["005930"]
    assert ctx.broker.account(Market.KR).positions["005930"].qty > 0
    plan = ctx.store.active_plans("paper", "KR")
    assert len(plan) == 1 and plan[0]["stop"] < plan[0]["take"]  # 손절·익절 계획까지
    why = ctx.store.rows("select message from orders")[0]["message"]
    assert "장중 회의 매수" in why and "체크리스트" in why
    live = __import__("qtrader.live", fromlist=["x"]).intraday_board(ctx, Market.KR)
    assert live["on"] and live["items"][0]["state"] == "bought" and live["met"] == 1


def test_same_symbol_no_second_meeting_or_buy(ctx, fake_meetings):
    _, opened = fake_meetings
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    ctx.store.set(f"picks_KR_{trade_date(Market.KR)}", json.dumps([{"symbol": "005930"}]))
    s._intraday_KR = float("-inf")  # 간격이 지났다고 치고
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]  # 같은 종목은 하루 한 번만 회의
    r = buy_one(ctx, Market.KR, "005930")  # 직접 다시 불러도 안 산다
    assert r["status"] in ("blocked", "skipped"), r  # 이미 목표만큼 가졌거나 오늘 이미 주문한 종목
    assert orders(ctx) == [("005930", "buy", "filled")]


def test_next_meeting_waits_for_interval_then_picks_new_symbol(ctx, fake_meetings):
    _, opened = fake_meetings
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]  # 15분 간격 전에는 다음 회의를 열지 않는다
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    assert opened == ["005930", "000660"]


def test_daily_meeting_cap_and_cost_cap(ctx, fake_meetings):
    _, opened = fake_meetings
    ctx.cfg["ai"]["max_symbols_per_day"] = 1
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]  # 하루 회의 수 상한 (정기 회의와 합산)
    ctx.cfg["ai"]["max_symbols_per_day"] = 8
    ctx.cfg["ai"]["monthly_cost_cap_krw"] = 1
    ctx.store.set("recheck_cost_" + __import__("datetime").datetime.now().strftime("%Y-%m"), "1")  # 이번 달 비용이 상한을 넘음
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]


def test_daily_new_buy_cap_blocks_buy(ctx, fake_meetings):
    start, _ = fake_meetings
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], max_new_buys_per_day=1)
    for sym in ("005930", "000660"):
        start(sym, ctx.data, ctx.store, ctx.cfg)
    assert buy_one(ctx, Market.KR, "005930")["status"] == "filled"
    r = buy_one(ctx, Market.KR, "000660")
    assert r["status"] == "skipped" and "신규 매수 한도" in r["message"]
    assert orders(ctx) == [("005930", "buy", "filled")]


def test_kill_switch_blocks_intraday(ctx, fake_meetings):
    start, opened = fake_meetings
    start("005930", ctx.data, ctx.store, ctx.cfg)
    ctx.store.set("kill_switch", "1")
    r = buy_one(ctx, Market.KR, "005930")
    assert r["status"] == "blocked" and "킬 스위치" in r["message"] and orders(ctx) == []
    opened.clear()
    Scheduler()._intraday(ctx, Market.KR)
    assert opened == []  # 긴급 정지 중에는 새 회의(비용)도 열지 않는다


def test_intraday_buy_never_sells(ctx, fake_meetings):
    """리밸런싱(run_market)과 달리 다른 보유 종목을 팔지 않는다. 매도 의견이 나와도 여기서는 팔지 않는다."""
    start, _ = fake_meetings
    start.sell = {"000660"}
    ctx.broker.order(OrderRequest(Market.KR, "000660", Side.BUY, 10, ctx.data.price(Market.KR, "000660"), "manual"))
    for sym in ("000660", "005930"):
        start(sym, ctx.data, ctx.store, ctx.cfg)
    assert buy_one(ctx, Market.KR, "000660")["status"] == "skipped"  # Sell 의견 -> 아무것도 안 함
    assert buy_one(ctx, Market.KR, "005930")["status"] == "filled"
    assert orders(ctx) == [("005930", "buy", "filled")]
    assert ctx.broker.account(Market.KR).positions["000660"].qty == 10


def test_intraday_respects_approval_setting(ctx, fake_meetings):
    start, _ = fake_meetings
    ctx.cfg["ai"]["require_approval"] = True
    start("005930", ctx.data, ctx.store, ctx.cfg)
    r = buy_one(ctx, Market.KR, "005930")
    assert r["status"] == "pending_approval" and orders(ctx) == [] and len(ctx.store.approvals("pending")) == 1


def test_intraday_off_does_nothing(ctx, fake_meetings):
    _, opened = fake_meetings
    ctx.cfg["ai"]["intraday"] = False
    Scheduler()._intraday(ctx, Market.KR)
    assert opened == [] and orders(ctx) == []


def test_intraday_config_validation():
    cfg = config.load()
    assert config.validate(cfg) == []
    assert cfg["ai"]["intraday"] is True and cfg["ai"]["intraday_every_min"] == 15
    for k, v in (("intraday_every_min", 1), ("intraday_every_min", "15"), ("recheck_every_min", 0), ("recheck_per_day", -1),
                 ("intraday", "yes")):
        bad = json.loads(json.dumps(cfg))
        bad["ai"][k] = v
        assert config.validate(bad), (k, v)


def test_toss_us_intraday_buy_is_not_loc():
    from qtrader.broker import TossBroker

    class Client:
        def order(self, req, client_order_id="", loc=False):
            self.loc = loc
            return None
    c = Client()
    b = TossBroker(c, us_loc=True)
    req = OrderRequest(Market.US, "AAPL", Side.BUY, 1, 100.0, "ai")
    b.order(req)
    assert c.loc is True  # 정기 실행은 그대로 LOC
    b.order(req, loc=False)
    assert c.loc is False  # 장중 매수는 바로 지정가
