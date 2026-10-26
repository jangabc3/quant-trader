import numpy as np
import pandas as pd
import pytest

from qtrader import backtest, config, indicators as ind, strategies
from qtrader.broker import PaperBroker
from qtrader.engine import build_context, plan_orders, run_market
from qtrader.kis import split_us
from qtrader.models import Market, OrderRequest, Side
from qtrader.risk import RiskLimits, cap_weights, check_orders, loss_breached
from qtrader.scheduler import due, next_run
from qtrader.storage import Store


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(up).dropna().iloc[-1] == pytest.approx(100.0)
    down = pd.Series(np.arange(60, 1, -1, dtype=float))
    assert ind.rsi(down).dropna().iloc[-1] == pytest.approx(0.0)
    r = ind.rsi(pd.Series(np.random.default_rng(0).normal(0, 1, 300)).cumsum() + 100).dropna()
    assert ((r >= 0) & (r <= 100)).all()


def test_bollinger_order():
    c = pd.Series(np.random.default_rng(1).normal(0, 1, 100)).cumsum() + 50
    lo, mid, hi = ind.bollinger(c)
    ok = lo.notna()
    assert (lo[ok] <= mid[ok]).all() and (mid[ok] <= hi[ok]).all()


@pytest.mark.parametrize("name", list(strategies.REGISTRY))
def test_strategy_weights_valid(name, panel_kr):
    w = strategies.build(name).weights(panel_kr)
    assert list(w.columns) == list(panel_kr)
    assert (w >= 0).all().all()
    assert (w.sum(axis=1) <= 1.0 + 1e-9).all()


def test_no_lookahead(panel_kr):
    """미래 데이터를 바꿔도 과거 비중이 변하면 안 된다."""
    for name in strategies.REGISTRY:
        s = strategies.build(name)
        full = s.weights(panel_kr)
        cut = {k: v.iloc[:400] for k, v in panel_kr.items()}
        part = s.weights(cut)
        pd.testing.assert_frame_equal(full.iloc[:400], part, check_freq=False, obj=name)


def test_momentum_picks_winner():
    idx = pd.bdate_range("2024-01-01", periods=300)
    mk = lambda g: pd.DataFrame({c: 100 * np.exp(g * np.arange(300)) for c in ["open", "high", "low", "close"]}
                                | {"volume": 1.0}, index=idx)
    p = {"WIN": mk(0.003), "FLAT": mk(0.0), "LOSE": mk(-0.002)}
    last = strategies.MomentumRank(lookback=60, skip=0, top_k=1, rebalance_days=20).latest(p)
    assert last == {"WIN": 1.0, "FLAT": 0.0, "LOSE": 0.0}


def test_backtest_costs_reduce_returns(panel_kr):
    s = strategies.build("trend")
    free = backtest.run(s, panel_kr, Market.KR, costs=backtest.Costs(0, 0, 0))
    paid = backtest.run(s, panel_kr, Market.KR)
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    assert set(paid.metrics) >= {"cagr", "max_drawdown", "sharpe", "total_return"}
    assert paid.metrics["max_drawdown"] <= 0


def test_risk_caps_and_kill():
    lim = RiskLimits(max_weight=0.2, max_exposure=0.5, daily_loss_limit=0.03)
    w = cap_weights({"A": 0.6, "B": 0.6, "C": 0.1}, lim)
    assert max(w.values()) <= 0.2 + 1e-12 and sum(w.values()) <= 0.5 + 1e-12
    assert loss_breached(100, 96.9, lim) and not loss_breached(100, 97.5, lim) and not loss_breached(None, 1, lim)
    buy = OrderRequest(Market.KR, "A", Side.BUY, 10, 1000)
    sell = OrderRequest(Market.KR, "B", Side.SELL, 5, 1000)
    out = check_orders([sell, buy], lim, {"A", "B"}, kill=True, already_today=set(), cash=1e9)
    assert out.allowed == [sell] and out.blocked[0][0] is buy
    out = check_orders([buy], lim, {"A"}, kill=False, already_today={"A"}, cash=1e9)
    assert not out.allowed
    out = check_orders([buy], lim, {"Z"}, kill=False, already_today=set(), cash=1e9)
    assert "허용 종목" in out.blocked[0][1]
    out = check_orders([buy], lim, {"A"}, kill=False, already_today=set(), cash=5000)
    assert "현금 부족" in out.blocked[0][1]


def test_plan_orders_sells_first_and_us_limit():
    lim = RiskLimits(max_order_value=1e12)
    o = plan_orders({"A": 0.5, "B": 0.0}, {"A": 100.0, "B": 50.0}, {"B": 10}, 10_000, Market.US, lim, "t", 0.01)
    assert [(x.symbol, x.side, x.qty) for x in o] == [("B", Side.SELL, 10), ("A", Side.BUY, 50)]
    assert o[1].price == 101.0 and o[0].price == 49.5
    lim2 = RiskLimits(max_order_value=1000)
    o = plan_orders({"A": 1.0}, {"A": 100.0}, {}, 10_000, Market.KR, lim2, "t")
    assert o[0].qty == 10


def test_paper_broker_round_trip(tmp_path):
    st = Store(tmp_path / "t.db")
    b = PaperBroker(st, {"KR": 1_000_000}, lambda m, s: 10_000.0)
    assert b.order(OrderRequest(Market.KR, "A", Side.BUY, 50, 10_000)).ok
    a = b.account(Market.KR)
    assert a.positions["A"].qty == 50 and a.cash < 500_000
    assert not b.order(OrderRequest(Market.KR, "A", Side.SELL, 51, 10_000)).ok
    assert not b.order(OrderRequest(Market.KR, "A", Side.BUY, 1000, 10_000)).ok
    assert b.order(OrderRequest(Market.KR, "A", Side.SELL, 50, 10_000)).ok
    a = b.account(Market.KR)
    assert "A" not in a.positions
    assert 990_000 < a.cash < 1_000_000  # 비용만큼 줄었다


def test_engine_paper_end_to_end(isolated_home):
    from qtrader.data import write_demo_csv
    cfg = config.load()
    cfg["markets"]["US"]["enabled"] = True
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    ctx = build_context()
    assert ctx.broker.name == "paper" and ctx.data.name == "csv"

    dry = run_market(ctx, Market.KR, dry_run=True)
    assert all(o["status"] == "dry_run" for o in dry["orders"])
    assert not ctx.broker.account(Market.KR).positions

    r = run_market(ctx, Market.KR)
    assert r["orders"] and all(o["status"] == "filled" for o in r["orders"])
    acct = ctx.broker.account(Market.KR)
    assert acct.positions
    for p in acct.positions.values():
        assert p.value <= cfg["markets"]["KR"]["budget"] * cfg["risk"]["max_weight"] * 1.01
    assert run_market(ctx, Market.KR) == {"skipped": "오늘 이미 실행했습니다"}

    us = run_market(ctx, Market.US)
    assert "error" not in us


def test_kill_switch_blocks_buys(isolated_home):
    from qtrader.data import write_demo_csv
    write_demo_csv(isolated_home / "data", config.load())
    ctx = build_context()
    ctx.store.set("kill_switch", "1")
    r = run_market(ctx, Market.KR)
    assert not [o for o in r["orders"] if o["side"] == "buy"]


def test_real_mode_requires_confirmation(monkeypatch):
    cfg = config.load()
    cfg["mode"], cfg["kis"]["account"] = "kis_real", "12345678-01"
    monkeypatch.setattr("qtrader.engine.vault.has_kis", lambda env: True)
    monkeypatch.setattr("qtrader.engine.vault.get_kis", lambda env: ("k" * 20, "s" * 20))
    with pytest.raises(RuntimeError, match="live_confirmed"):
        build_context(cfg)
    cfg["live_confirmed"] = True
    assert build_context(cfg).broker.live


def test_config_validation():
    cfg = config.load()
    assert config.validate(cfg) == []
    cfg["mode"] = "kis_demo"
    assert any("account" in e for e in config.validate(cfg))
    cfg["markets"]["KR"]["run_time"] = "25:00"
    assert any("run_time" in e for e in config.validate(cfg))


def test_us_symbol_parsing():
    assert split_us("AAPL") == ("NASD", "AAPL")
    assert split_us("nyse:ko") == ("NYSE", "KO")
    with pytest.raises(ValueError):
        split_us("LSE:VOD")


def test_schedule_times():
    from qtrader.engine import TZ
    kr = TZ[Market.KR]
    mon_1511 = pd.Timestamp("2026-10-26 15:11", tz=kr).to_pydatetime()
    mon_1530 = pd.Timestamp("2026-10-26 15:30", tz=kr).to_pydatetime()
    sat = pd.Timestamp("2026-10-24 15:11", tz=kr).to_pydatetime()
    assert due(Market.KR, "15:10", mon_1511) and not due(Market.KR, "15:10", mon_1530)
    assert not due(Market.KR, "15:10", sat)
    fri = pd.Timestamp("2026-10-23 16:00", tz=kr).to_pydatetime()
    assert next_run(Market.KR, "15:10", fri).weekday() == 0
    # 뉴욕 서머타임 해제(11/1) 후에도 현지 15:45
    ny = TZ[Market.US]
    after = pd.Timestamp("2026-11-02 15:46", tz=ny).to_pydatetime()
    assert due(Market.US, "15:45", after)
