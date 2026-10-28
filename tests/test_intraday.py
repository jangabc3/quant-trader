"""장중 계속 사기: 사는 신호 -> AI 거부권 -> 그 종목만 바로 매수 (리밸런싱 없음), 비용·안전 한도."""
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

    def start(symbol, data, store, cfg, llm=None, toss=None, background=True, origin="manual"):
        opened.append(symbol)
        rating = "Sell" if symbol in start.sell else "Buy"
        return real(symbol, data, store, cfg, llm=FakeLLM(rating, 0.9), toss=toss, background=False, origin=origin)
    start.sell = set()
    monkeypatch.setattr(meeting, "start", start)
    return start, opened


@pytest.fixture
def fake_signals(monkeypatch):
    """신호는 정해 둔 종목에만 뜨고, 거부권은 가짜 Claude 가 바로 답한다 (거부할 종목을 정할 수 있다)."""
    from qtrader import signals
    calls, fire, reject = [], {"005930", "000660"}, set()

    def scan(ctx, m, symbols, today):
        return [{"symbol": s, "kind": "pullback", "name": "눌림목 반등", "why": "20일선에서 반등 (시험)",
                 "price": float(ctx.data.price(m, s)), "atr": 1.0, "rsi": 55.0, "stop_pct": 5.0, "take_pct": 10.0,
                 "pace": 1.0, "sma20": 1.0, "sma200": 1.0} for s in symbols if s in fire]

    def veto(ctx, m, symbol, name, sig, llm=None):
        calls.append(symbol)
        bad = symbol in reject
        return {"veto": bad, "confidence": 0.8, "reason": "유상증자 공시" if bad else "뚜렷한 위험 신호 없음",
                "risks": ["유상증자"] if bad else [], "positives": ["추세 상승"], "cost_usd": 0.01, "context": "자료", "warnings": []}
    monkeypatch.setattr(signals, "scan", scan)
    monkeypatch.setattr(signals, "veto", veto)
    monkeypatch.setattr(signals, "ai_ready", lambda: True)
    return calls, fire, reject


def orders(ctx):
    return [(o["symbol"], o["side"], o["status"]) for o in ctx.store.rows("select * from orders order by id")]


def test_signal_then_veto_then_one_buy(ctx, fake_signals):
    calls, _, _ = fake_signals
    out = Scheduler()._intraday(ctx, Market.KR)
    assert calls == ["005930"] and [(o["symbol"], o["status"]) for o in out] == [("005930", "filled")]  # 한 틱에 한 종목
    assert orders(ctx) == [("005930", "buy", "filled")]
    plan = ctx.store.active_plans("paper", "KR")
    assert len(plan) == 1 and plan[0]["stop"] < plan[0]["take"]  # 손절·익절 계획까지
    why = ctx.store.rows("select message from orders")[0]["message"]
    assert "AI 거부권 통과" in why and "사는 신호(눌림목 반등)" in why and "-5.0%" in why
    st = json.loads(ctx.store.get(f"intraday_KR_{trade_date(Market.KR)}"))
    assert st["005930"]["state"] == "bought" and st["005930"]["signal"] == "눌림목 반등"
    run = ctx.store.ai_runs(5)[0]  # 판단 기록: 회의처럼 남아 채점표·화면이 읽는다
    assert run["origin"] == "signal" and run["final"]["rating"] == "Overweight" and run["checklist"]["passed"]
    assert any(r["name"].startswith("AI 거부권 통과") for r in run["checklist"]["rules"])
    assert not any(r["key"] == "facts" for r in run["checklist"]["rules"])  # 팩트체크 대신 거부권이 공시·뉴스를 본다
    live = __import__("qtrader.live", fromlist=["x"]).intraday_board(ctx, Market.KR)
    assert live["on"] and live["items"][0]["state"] == "bought" and live["met"] == 1


def test_no_signal_no_ai_call(ctx, fake_signals):
    calls, fire, _ = fake_signals
    fire.clear()
    assert Scheduler()._intraday(ctx, Market.KR) == [] and calls == [] and orders(ctx) == []  # 신호가 없으면 비용 0


def test_vetoed_signal_is_not_bought(ctx, fake_signals):
    calls, _, reject = fake_signals
    reject.add("005930")
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    assert calls == ["005930"] and orders(ctx) == []
    st = json.loads(ctx.store.get(f"intraday_KR_{trade_date(Market.KR)}"))
    assert st["005930"]["state"] == "vetoed" and "유상증자" in st["005930"]["note"]
    assert ctx.store.ai_runs(1)[0]["final"]["rating"] == "Hold"
    s._intraday(ctx, Market.KR)  # 다음 틱: 거부된 종목은 다시 묻지 않고 다음 신호로
    assert calls == ["005930", "000660"] and orders(ctx) == [("000660", "buy", "filled")]


def test_same_symbol_once_per_day(ctx, fake_signals):
    calls, _, _ = fake_signals
    s = Scheduler()
    for _ in range(4):
        s._intraday(ctx, Market.KR)
    assert calls == ["005930", "000660"]  # 같은 종목은 하루 한 번만 확인
    r = buy_one(ctx, Market.KR, "005930")  # 직접 다시 불러도 안 산다
    assert r["status"] in ("blocked", "skipped"), r
    assert orders(ctx) == [("005930", "buy", "filled"), ("000660", "buy", "filled")]


def test_daily_veto_cap_and_cost_cap(ctx, fake_signals):
    calls, _, _ = fake_signals
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 1
    s = Scheduler()
    s._intraday(ctx, Market.KR)
    s._intraday(ctx, Market.KR)
    assert calls == ["005930"]  # 하루 거부권 확인 수 상한
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 8
    ctx.cfg["ai"]["monthly_cost_cap_krw"] = 1
    ctx.store.set("recheck_cost_" + __import__("datetime").datetime.now().strftime("%Y-%m"), "1")  # 이번 달 비용이 상한을 넘음
    s._intraday(ctx, Market.KR)
    assert calls == ["005930"]


def test_real_signal_rules():
    """눌림목 반등: 오르는 추세에서 20일선까지 눌렸다가 어제 고가를 넘을 때. 거래량 돌파: 20일 최고가 + 거래량 1.5배 속도.
    너무 오른 자리·과열·하락 추세는 신호가 아니다."""
    import numpy as np
    import pandas as pd
    from qtrader import signals
    days = pd.bdate_range(end="2026-10-27", periods=260)
    base = np.linspace(100, 160, 260) + np.sin(np.arange(260) / 3) * 0.6
    df = pd.DataFrame({"close": base, "high": base * 1.01, "low": base * 0.99, "volume": 1e6}, index=days)
    # 마지막 6일 동안 20일선까지 내려옴 (진짜 눌림: RSI 가 식는다)
    peak, floor = base[-7], df["close"].rolling(20).mean().iloc[-7] * 0.995
    drop = np.linspace(peak, floor, 7)[1:]
    df.iloc[-6:, df.columns.get_loc("close")] = drop
    df.iloc[-6:, df.columns.get_loc("high")] = drop * 1.004
    df.iloc[-6:, df.columns.get_loc("low")] = drop * 0.996
    sma20 = df["close"].rolling(20).mean().iloc[-1]
    today = "2026-10-28"
    yhigh = float(df["high"].iloc[-1])
    sig = signals.check(df, yhigh * 1.01, today)
    assert sig and sig["kind"] == "pullback" and 3 <= sig["stop_pct"] <= 10 and sig["take_pct"] == sig["stop_pct"] * 2, sig
    assert signals.check(df, yhigh * 0.999, today) is None  # 아직 어제 고가를 못 넘음
    assert signals.check(df, float(sma20 * 1.2), today) is None  # 너무 멀리 오름
    down = df.iloc[::-1].copy()
    down.index = days
    assert signals.check(down, float(down["close"].iloc[-1] * 1.03), today) is None  # 내리막 추세
    # 돌파: 한 달 옆으로 쉬다가 20일 최고가를 거래량과 함께 넘는 경우 (오늘 봉이 같이 온다)
    flat = base.copy()
    flat[-30:] = base[-31] + np.sin(np.arange(30)) * 1.5
    up = pd.DataFrame({"close": flat, "high": flat * 1.01, "low": flat * 0.99, "volume": 1e6}, index=days)
    top = float(up["high"].tail(20).max())
    now = pd.DataFrame({"close": [top * 1.01], "high": [top * 1.01], "low": [flat[-1]], "volume": [2e6]},
                       index=pd.to_datetime([today]))
    sig = signals.check(pd.concat([up, now]), top * 1.01, today, frac=1.0)
    assert sig and sig["kind"] == "breakout" and sig["pace"] >= 1.5
    weak = signals.check(pd.concat([up, now.assign(volume=[1e6])]), top * 1.01, today)
    assert not weak or weak["kind"] != "breakout"  # 거래량이 평소 같으면 돌파로 보지 않는다


def test_daily_new_buy_cap_blocks_buy(ctx, fake_meetings):
    start, _ = fake_meetings
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], max_new_buys_per_day=1)
    for sym in ("005930", "000660"):
        start(sym, ctx.data, ctx.store, ctx.cfg)
    assert buy_one(ctx, Market.KR, "005930")["status"] == "filled"
    r = buy_one(ctx, Market.KR, "000660")
    assert r["status"] == "skipped" and "신규 매수 한도" in r["message"]
    assert orders(ctx) == [("005930", "buy", "filled")]


def test_kill_switch_blocks_intraday(ctx, fake_meetings, fake_signals):
    start, opened = fake_meetings
    calls, _, _ = fake_signals
    start("005930", ctx.data, ctx.store, ctx.cfg)
    ctx.store.set("kill_switch", "1")
    r = buy_one(ctx, Market.KR, "005930")
    assert r["status"] == "blocked" and "킬 스위치" in r["message"] and orders(ctx) == []
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == []  # 긴급 정지 중에는 거부권 확인(비용)도 하지 않는다


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


def test_intraday_off_does_nothing(ctx, fake_signals):
    calls, _, _ = fake_signals
    ctx.cfg["ai"]["intraday"] = False
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == [] and orders(ctx) == []


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


def test_after_hours_tick_buys_then_stops_out(ctx, fake_signals, monkeypatch):
    """정규장이 아닌 시간(넥스트레이드)에도 2분 점검이 신호 -> 거부권 -> 매수 -> 손절 매도까지 혼자 한다."""
    from qtrader import scheduler
    calls, fire, _ = fake_signals
    fire.discard("000660")
    monkeypatch.setattr(scheduler, "trading_now", lambda cfg, m, now=None: m == Market.KR)
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: False)
    monkeypatch.setattr(scheduler, "due", lambda *a, **k: False)  # 정기 실행은 이 시험에서 빼고
    ctx.store.set("auto_trading", "1")
    s = scheduler.Scheduler()
    s.tick()  # 신호 -> 거부권 -> 매수
    assert calls == ["005930"] and orders(ctx) == [("005930", "buy", "filled")]
    ctx.store.exec("update plans set stop=? where symbol='005930'", (10 ** 9,))  # 손절가를 현재가 위로
    s._last_exit = float("-inf")
    s.tick()  # 손절
    assert orders(ctx) == [("005930", "buy", "filled"), ("005930", "sell", "filled")]
    sell = ctx.store.rows("select * from orders where side='sell'")[0]
    assert sell["price"] > 0 and "손절" in sell["message"]  # 시간외라 시장가가 아닌 지정가


def test_no_veto_when_buy_limit_already_reached(ctx, fake_meetings, fake_signals):
    """오늘 살 수 있는 종목 수를 다 채웠으면, 신호가 떠도 못 사므로 거부권 확인(비용)을 하지 않는다."""
    start, _ = fake_meetings
    calls, _, _ = fake_signals
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], max_new_buys_per_day=1)
    start("005930", ctx.data, ctx.store, ctx.cfg)
    assert buy_one(ctx, Market.KR, "005930")["status"] == "filled"
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == []
    assert any("장중 매수 쉼" in e["message"] and "하루 한도" in e["message"] for e in ctx.store.rows("select * from events"))


def test_no_veto_when_market_regime_blocks_buys(ctx, fake_signals, monkeypatch):
    import pandas as pd
    from qtrader import principles
    calls, _, _ = fake_signals
    ctx.cfg["ai"]["rules"] = dict(ctx.cfg["ai"]["rules"], market_filter="block")
    down = pd.DataFrame({"close": [300 - i * 0.5 for i in range(300)]})
    monkeypatch.setattr(principles, "index_bars", lambda data, m: down)
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == []  # 지수가 200일선 아래 + '매수 중단' 규칙
    monkeypatch.setattr(principles, "index_bars", lambda data, m: down.iloc[::-1].reset_index(drop=True))
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == ["005930"]  # 상승 국면이면 확인한다


def test_vetoes_are_counted_per_market(ctx, fake_signals):
    """AI 거부권 확인은 국내·미국 따로 세고, 한 시장만 끌 수 있다 (끄면 그 시장은 새로 사지 않는다)."""
    from qtrader.config import meetings_cap
    calls, _, _ = fake_signals
    ctx.cfg["markets"]["KR"]["ai_meetings"] = False
    assert meetings_cap(ctx.cfg, Market.KR) == 0 and meetings_cap(ctx.cfg, Market.US) == 6
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == [] and orders(ctx) == []


def test_old_total_meeting_cap_split_in_half(isolated_home):
    """예전 '하루 회의 6번'(합계) → 시장별로 나눈 뒤, 거부권 확인은 회의보다 30배쯤 싸서 최소 6번으로."""
    old = json.loads(json.dumps(config.DEFAULT))
    old.pop("migrated")
    old["ai"]["max_symbols_per_day"] = 6
    for m in ("KR", "US"):
        old["markets"][m].pop("meetings_per_day")
    config.path().write_text(json.dumps(old), encoding="utf-8")
    cfg = config.load()
    assert cfg["markets"]["KR"]["meetings_per_day"] == 6 and cfg["markets"]["US"]["meetings_per_day"] == 6
    assert "max_symbols_per_day" not in cfg["ai"] and config.validate(cfg) == []
    cfg["markets"]["KR"]["meetings_per_day"] = 2  # 그 뒤 사용자가 줄이면 그대로
    config.save(cfg)
    assert config.load()["markets"]["KR"]["meetings_per_day"] == 2


def test_today_plan_explains_what_auto_trading_is_doing(ctx, fake_signals, monkeypatch):
    """'오늘 자동매매' 판: 지금 무엇을 하는지 한 줄 + 후보마다 단계 (신호 기다림 → 샀어요 / 거부)."""
    from qtrader import live, scheduler
    calls, _, reject = fake_signals
    p = live.today_plan(ctx, Market.KR)
    assert "자동매매가 꺼져" in p["headline"] and "사는 신호" in p["headline"] and [i["state"] for i in p["items"]] == ["wait", "wait"]
    ctx.store.set("auto_trading", "1")
    monkeypatch.setattr(scheduler, "trading_now", lambda cfg, m, now=None: True)
    p = live.today_plan(ctx, Market.KR)
    assert "2분마다" in p["headline"] and [i["state"] for i in p["items"]] == ["watch", "watch"]
    reject.add("000660")
    s = Scheduler()
    s._intraday(ctx, Market.KR)  # 005930 매수
    s._intraday(ctx, Market.KR)  # 000660 거부
    p = live.today_plan(ctx, Market.KR)
    by = {i["symbol"]: i for i in p["items"]}
    assert by["005930"]["state"] == "bought" and by["000660"]["state"] == "vetoed" and p["used"] == 2 and p["last_check"]
    assert "모두 확인했어요" in p["headline"]
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


def test_manual_meeting_does_not_buy_or_use_veto_cap(ctx, fake_meetings, fake_signals):
    """직접 연 7명 회의는 자동으로 사지 않고 (사는 신호를 기다린다), 하루 거부권 확인 수에도 넣지 않는다."""
    from qtrader import live
    start, _ = fake_meetings
    calls, fire, _ = fake_signals
    fire.clear()
    ctx.cfg["markets"]["KR"]["meetings_per_day"] = 1
    start("000660", ctx.data, ctx.store, ctx.cfg)  # 사용자가 직접 (결론 매수)
    Scheduler()._intraday(ctx, Market.KR)
    assert orders(ctx) == [] and calls == []
    plan = live.today_plan(ctx, Market.KR)
    assert plan["used"] == 0 and plan["manual"] == 1 and plan["cap"] == 1
    fire.add("000660")
    Scheduler()._intraday(ctx, Market.KR)  # 신호가 뜨면 그때 거부권 -> 매수
    assert calls == ["000660"] and orders(ctx) == [("000660", "buy", "filled")]


def test_veto_reads_free_sources_and_fails_safe(ctx, monkeypatch):
    """거부권: 웹 검색 없이 공시·뉴스 제목·투자 유의만 읽고 Claude 1번. 실패하면 사지 않는다."""
    from qtrader import news, screener, signals
    from qtrader.agents.llm import LLMError, Reply
    monkeypatch.setattr(screener, "disclosure_titles", lambda s, h, d=10: ["유상증자결정"])
    monkeypatch.setattr(news, "stock_news", lambda q, n=20: [{"title": "실적 쇼크", "published": "2026-10-28"}])
    seen = {}

    class LLM:
        def run(self, system, user, web=False, schema=None, max_tokens=0, model=None, context=None, effort=None, searches=None):
            seen.update(web=web, context=context, effort=effort)
            d = {"veto": True, "confidence": 0.2, "reason": "유상증자 공시", "risks": ["유상증자"], "positives": []}
            return Reply(json.dumps(d), data=d, input_tokens=1000, output_tokens=100, model="claude-sonnet-5-5")
    sig = {"symbol": "005930", "kind": "pullback", "name": "눌림목 반등", "why": "w", "price": 100.0, "atr": 2.0, "rsi": 55.0,
           "stop_pct": 4.0, "take_pct": 8.0, "pace": 0.0, "sma20": 98.0, "sma200": 90.0}
    v = signals.veto(ctx, Market.KR, "005930", "삼성전자", sig, llm=LLM())
    assert v["veto"] and v["cost_usd"] > 0 and seen["web"] is False and seen["effort"] == "low"
    assert "유상증자결정" in seen["context"] and "실적 쇼크" in seen["context"]
    run = signals.save_decision(ctx, Market.KR, "005930", "삼성전자", sig, v, trade_date(Market.KR))
    assert run["final"]["rating"] == "Hold" and not run["checklist"]["passed"]
    saved = ctx.store.latest_decision("005930", trade_date(Market.KR))
    assert saved["origin"] == "signal" and saved["signal"]["name"] == "눌림목 반등"
    assert buy_one(ctx, Market.KR, "005930")["status"] == "skipped"

    class Down:
        def run(self, *a, **k):
            raise LLMError("연결 안 됨")
    v = signals.veto(ctx, Market.KR, "005930", "삼성전자", sig, llm=Down())
    assert v["veto"] and "확인 실패" in v["reason"]  # AI 를 못 부르면 사지 않는다


def test_no_claude_key_no_veto(ctx, fake_signals, monkeypatch):
    """Claude 키가 없으면 거부권을 묻지 않는다 (실패로 '거부'가 쌓이고 하루 확인 수만 줄던 문제)."""
    from qtrader import live, scheduler, signals
    calls, _, _ = fake_signals
    monkeypatch.setattr(signals, "ai_ready", lambda: False)
    Scheduler()._intraday(ctx, Market.KR)
    assert calls == [] and orders(ctx) == []
    ctx.store.set("auto_trading", "1")
    monkeypatch.setattr(scheduler, "trading_now", lambda cfg, m, now=None: True)
    assert "Claude API 키" in live.today_plan(ctx, Market.KR)["headline"]
