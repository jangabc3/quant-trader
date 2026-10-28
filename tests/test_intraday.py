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
    cfg["ai"]["practice"] = False  # 가상매매 연습 기준 없이, 실전과 같은 규칙으로 시험
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
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 1
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]  # 하루 회의 수 상한 (정기 회의와 합산)
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 8
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

def test_old_config_moves_to_all_sessions_once(isolated_home):
    """예전에 저장한 설정(시간외 꺼짐)은 한 번 모든 세션으로 바뀌고, 그 뒤 사용자가 끄면 그대로 둔다."""
    old = json.loads(json.dumps(config.DEFAULT))
    old.pop("migrated")
    old["markets"]["KR"]["extended"] = False
    old["markets"]["US"]["extended_sessions"] = []
    config.path().write_text(json.dumps(old), encoding="utf-8")
    cfg = config.load()
    assert cfg["markets"]["KR"]["extended"] and cfg["markets"]["US"]["extended_sessions"] == ["day", "pre", "after"]
    cfg["markets"]["KR"]["extended"] = False  # 사용자가 넥스트레이드를 끄고 저장
    config.save(cfg)
    assert config.load()["markets"]["KR"]["extended"] is False

def test_recheck_sell_outside_regular_hours_is_limit(ctx, monkeypatch):
    """넥스트레이드 시간에는 국내 시장가가 안 되므로 AI 재점검 매도도 지정가로."""
    from qtrader import live, scheduler
    from qtrader.agents.llm import Reply
    px = ctx.data.price(Market.KR, "000660")
    ctx.broker.order(OrderRequest(Market.KR, "000660", Side.BUY, 5, px, "manual"))
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: False)
    monkeypatch.setattr(live, "triggers", lambda c, m, w: [("000660", "장중 -5% 급락")])

    class Sell:
        def run(self, *a, **k):
            d = {"action": "sell", "reason": "악재 확인", "watch": "-"}
            return Reply(json.dumps(d), data=d, input_tokens=1, output_tokens=1)
    out = live.run_rechecks(ctx, Market.KR, llm=Sell())
    assert out and out[0]["action"] == "sell"
    o = ctx.store.rows("select * from orders where side='sell'")[0]
    assert o["status"] == "filled" and 0 < o["price"] < px
    assert "000660" not in ctx.broker.account(Market.KR).positions

def test_all_sessions_by_default_and_names():
    """프리장·장중·애프터장 내내: 기본으로 모든 세션에서 감시·사고판다."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from qtrader.scheduler import session_name, trading_now
    cfg = config.load()
    kst, ny = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
    at = lambda tz, h, mi=0: datetime(2026, 10, 28, h, mi, tzinfo=tz)  # 수요일
    assert session_name(cfg, Market.KR, at(kst, 8, 30)) == "넥스트레이드"
    assert session_name(cfg, Market.KR, at(kst, 10)) == "정규장"
    assert session_name(cfg, Market.KR, at(kst, 19, 30)) == "넥스트레이드"
    assert session_name(cfg, Market.KR, at(kst, 21)) is None
    assert session_name(cfg, Market.US, at(ny, 5)) == "프리마켓"
    assert session_name(cfg, Market.US, at(ny, 10)) == "정규장"
    assert session_name(cfg, Market.US, at(ny, 17)) == "애프터마켓"
    assert session_name(cfg, Market.US, at(kst, 11)) == "주간거래"
    assert trading_now(cfg, Market.US, at(ny, 17)) and not trading_now(cfg, Market.US, at(ny, 20, 30))


def test_after_hours_tick_buys_then_stops_out(ctx, fake_meetings, monkeypatch):
    """정규장이 아닌 시간(넥스트레이드)에도 2분 점검이 회의 -> 매수 -> 손절 매도까지 혼자 한다."""
    from qtrader import scheduler
    _, opened = fake_meetings
    monkeypatch.setattr(scheduler, "trading_now", lambda cfg, m, now=None: m == Market.KR)
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: False)
    monkeypatch.setattr(scheduler, "due", lambda *a, **k: False)  # 정기 실행은 이 시험에서 빼고
    monkeypatch.setattr(scheduler.Scheduler, "_ai_meetings", lambda *a, **k: None)
    ctx.store.set("auto_trading", "1")
    s = scheduler.Scheduler()
    s.tick()  # 회의 열기
    s._last_exit = float("-inf")
    s.tick()  # 결론 -> 매수
    assert opened == ["005930"] and orders(ctx) == [("005930", "buy", "filled")]
    ctx.store.exec("update plans set stop=? where symbol='005930'", (10 ** 9,))  # 손절가를 현재가 위로
    s._last_exit = float("-inf")
    s.tick()  # 손절
    assert orders(ctx) == [("005930", "buy", "filled"), ("005930", "sell", "filled")]
    sell = ctx.store.rows("select * from orders where side='sell'")[0]
    assert sell["price"] > 0 and "손절" in sell["message"]  # 시간외라 시장가가 아닌 지정가


def test_no_meeting_when_buy_limit_already_reached(ctx, fake_meetings):
    """오늘 살 수 있는 종목 수를 다 채웠으면, 결론이 매수여도 못 사므로 회의(비용)를 열지 않는다."""
    start, opened = fake_meetings
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], max_new_buys_per_day=1)
    start("005930", ctx.data, ctx.store, ctx.cfg)
    assert buy_one(ctx, Market.KR, "005930")["status"] == "filled"
    opened.clear()
    Scheduler()._intraday(ctx, Market.KR)
    assert opened == []
    assert any("장중 회의 쉼" in e["message"] and "하루 한도" in e["message"] for e in ctx.store.rows("select * from events"))


def test_no_meeting_when_market_regime_blocks_buys(ctx, fake_meetings, monkeypatch):
    import pandas as pd
    from qtrader import principles
    _, opened = fake_meetings
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], market_filter="block")
    down = pd.DataFrame({"close": [300 - i * 0.5 for i in range(300)]})
    monkeypatch.setattr(principles, "index_bars", lambda data, m: down)
    Scheduler()._intraday(ctx, Market.KR)
    assert opened == []  # 지수가 200일선 아래 + '매수 중단' 규칙
    monkeypatch.setattr(principles, "index_bars", lambda data, m: down.iloc[::-1].reset_index(drop=True))
    Scheduler()._intraday(ctx, Market.KR)
    assert opened == ["005930"]  # 상승 국면이면 연다


def test_meetings_are_counted_per_market(ctx, fake_meetings):
    """AI 회의는 국내·미국 따로: 하루 회의 수를 시장마다 세고, 한 시장만 끌 수 있다."""
    from qtrader.config import meetings_cap, met_today
    start, opened = fake_meetings
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 1
    start("AAPL", ctx.data, ctx.store, ctx.cfg)  # 미국 회의는 국내 한도에 안 센다
    assert met_today(ctx.store, Market.KR, trade_date(Market.KR)) == set()
    opened.clear()
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]
    ctx.cfg["markets"]["KR"]["ai_meetings"] = False  # 국내 AI 회의 끄기
    assert meetings_cap(ctx.cfg, Market.KR) == 0 and meetings_cap(ctx.cfg, Market.US) == 4
    s._intraday(ctx, Market.KR)  # 결론 -> 매수는 그대로
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    assert opened == ["005930"]


def test_old_total_meeting_cap_split_in_half(isolated_home):
    old = json.loads(json.dumps(config.DEFAULT))
    old.pop("migrated")
    old["ai"]["max_symbols_per_day"] = 6
    for m in ("KR", "US"):
        old["markets"][m].pop("meetings_per_day")
    config.path().write_text(json.dumps(old), encoding="utf-8")
    cfg = config.load()
    assert cfg["markets"]["KR"]["meetings_per_day"] == 3 and cfg["markets"]["US"]["meetings_per_day"] == 3
    assert "max_symbols_per_day" not in cfg["ai"] and config.validate(cfg) == []


def test_today_plan_explains_what_auto_meeting_is_doing(ctx, fake_meetings, monkeypatch):
    """AI 사무실 '오늘 AI 회의' 판: 지금 무엇을 하는지 한 줄 + 후보마다 단계."""
    from qtrader import live, scheduler
    start, _ = fake_meetings
    p = live.today_plan(ctx, Market.KR)
    assert "자동매매가 꺼져" in p["headline"] and [i["state"] for i in p["items"]] == ["wait", "wait"]
    ctx.store.set("auto_trading", "1")
    monkeypatch.setattr(scheduler, "trading_now", lambda cfg, m, now=None: True)
    assert "다음 회의는 005930" in live.today_plan(ctx, Market.KR)["headline"]
    start.sell = {"000660"}
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)  # 005930 매수
    s._intraday_KR = float("-inf")
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)  # 000660 매도 의견
    p = live.today_plan(ctx, Market.KR)
    by = {i["symbol"]: i for i in p["items"]}
    assert by["005930"]["state"] == "bought" and by["000660"]["state"] == "sell" and p["used"] == 2 and p["next_at"]
    assert "모두 회의했어요" in p["headline"]
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 2
    assert "2번을 다 썼어요" in live.today_plan(ctx, Market.KR)["headline"]


def test_picking_candidates_runs_in_background(ctx, monkeypatch):
    """오늘 후보 고르기(몇십 초~몇 분)는 뒤에서 한다. 그동안 점검은 기다리지 않고 손절·익절 감시를 계속한다."""
    import threading
    from qtrader import scheduler, screener
    from qtrader import engine
    today = trade_date(Market.KR)
    ctx.store.exec("delete from state where key=?", (f"picks_KR_{today}",))
    gate = threading.Event()

    def slow(c, m, t, n, stats=None):
        gate.wait(5)
        c.store.set(f"picks_KR_{t}", json.dumps([{"symbol": "005930"}]))
        return [{"symbol": "005930"}]
    monkeypatch.setattr(screener, "todays", slow)
    monkeypatch.setattr(engine, "build_context", lambda *a, **k: ctx)
    assert scheduler.todays_later(ctx, Market.KR, today, 4) == []  # 바로 돌아온다
    assert scheduler.todays_later(ctx, Market.KR, today, 4) == []  # 고르는 중엔 또 시작하지 않는다
    gate.set()
    for _ in range(50):
        if ctx.store.get(f"picks_KR_{today}"):
            break
        __import__("time").sleep(0.05)
    assert scheduler.todays_later(ctx, Market.KR, today, 4) == [{"symbol": "005930"}]


def test_paper_practice_rules_loosen_only_in_paper(ctx):
    """가상매매 연습 기준: 가상매매일 때만 확신도·손익비·추가 기준 허용 수·하루 매수 수를 조금 낮춘다. 실전에는 안 씀."""
    from qtrader import principles
    cfg = json.loads(json.dumps(ctx.cfg))
    cfg["ai"]["rules"] = {"min_confidence": 0.7, "min_reward_risk": 2.2, "soft_miss_allowed": 0, "max_new_buys_per_day": 1}
    assert principles.rules_for(cfg) == cfg["ai"]["rules"]  # practice 꺼짐
    cfg["ai"]["practice"] = True
    r = principles.rules_for(cfg)
    assert r["min_confidence"] == 0.5 and r["min_reward_risk"] == 1.5 and r["soft_miss_allowed"] == 2 and r["max_new_buys_per_day"] == 5
    cfg["mode"] = "toss_real"
    assert principles.rules_for(cfg) == cfg["ai"]["rules"]  # 실전에서는 절대 낮추지 않는다
