"""팔기 규칙 (style='trail'): +1R 절반 익절 → 손절 본전 → 따라 올라가는 손절, 기간 정리, 예전 방식(전부 손절·익절)."""
import json

import pytest

from qtrader import config
from qtrader.data import write_demo_csv
from qtrader.engine import build_context, check_exits
from qtrader.models import Market, OrderRequest, Side


@pytest.fixture
def ctx(isolated_home, monkeypatch):
    cfg = config.load()
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    c = build_context()
    px = {"v": 100.0}
    c.broker.price = lambda m, s: px["v"]
    c.data.price = lambda m, s: px["v"]
    from qtrader import scheduler
    monkeypatch.setattr(scheduler, "is_open", lambda m, now=None: True)  # 정규장: 국내 시장가
    c.broker.order(OrderRequest(Market.KR, "005930", Side.BUY, 10, 100.0, "ai"))
    # 진입 100, 손절 95 (R = 5), 처음 익절 110 (+2R)
    c.store.save_plan("paper", "KR", "005930", "r1", 100.0, 95.0, 110.0, 10, "trail", 5.0, 100.0)
    c.px = px
    return c


def plan(c):
    return c.store.active_plans("paper", "KR")[0]


def qty(c):
    p = c.broker.account(Market.KR).positions.get("005930")
    return p.qty if p else 0


def test_half_at_1r_then_breakeven_then_trailing_stop(ctx):
    ctx.px["v"] = 103.0
    assert check_exits(ctx, Market.KR) == [] and plan(ctx)["high"] == 103.0  # 아직 +1R 전
    ctx.px["v"] = 105.0  # +1R
    out = check_exits(ctx, Market.KR)
    assert [x["reason"] for x in out] == ["half"] and qty(ctx) == 5
    p = plan(ctx)
    assert p["half"] == 1 and p["stop"] == 100.0 and p["take"] == 130.0  # 본전 손절, 남은 절반은 크게 열어 둠 (+6R)
    sell = ctx.store.rows("select * from orders where side='sell'")[0]
    assert "절반 익절" in sell["message"] and sell["qty"] == 5
    ctx.px["v"] = 112.0  # 최고가 112 → 손절 112 - 7.5 = 104.5 (0.25R 넘게 올라서 고침)
    assert check_exits(ctx, Market.KR) == []
    assert plan(ctx)["stop"] == 104 and plan(ctx)["high"] == 112.0  # 국내 호가 단위로 내림
    ctx.px["v"] = 112.5  # 조금만 더 오르면 고치지 않는다 (토스 보호를 너무 자주 바꾸지 않게)
    check_exits(ctx, Market.KR)
    assert plan(ctx)["stop"] == 104
    ctx.px["v"] = 103.0  # 따라 올라간 손절에 닿으면 남은 것 모두
    out = check_exits(ctx, Market.KR)
    assert [x["reason"] for x in out] == ["stop"] and qty(ctx) == 0 and ctx.store.active_plans("paper", "KR") == []


def test_stop_before_half_sells_all(ctx):
    ctx.px["v"] = 94.0
    out = check_exits(ctx, Market.KR)
    assert [x["reason"] for x in out] == ["stop"] and qty(ctx) == 0


def test_time_exit_when_it_does_not_move(ctx):
    ctx.store.exec("update plans set created_at='2026-01-01T00:00:00+00:00'")
    ctx.px["v"] = 101.0
    out = check_exits(ctx, Market.KR)
    assert [x["reason"] for x in out] == ["time"] and qty(ctx) == 0
    assert "기간 정리" in ctx.store.rows("select * from orders where side='sell'")[0]["message"]


def test_no_time_exit_after_half(ctx):
    ctx.px["v"] = 105.0
    check_exits(ctx, Market.KR)
    ctx.store.exec("update plans set created_at='2026-01-01T00:00:00+00:00'")
    ctx.px["v"] = 106.0
    assert check_exits(ctx, Market.KR) == [] and qty(ctx) == 5  # 절반 익절 뒤에는 따라 올라가는 손절이 맡는다


def test_one_share_moves_stop_without_selling(ctx):
    ctx.broker.order(OrderRequest(Market.KR, "005930", Side.SELL, 9, 100.0, "manual"))
    ctx.px["v"] = 105.0
    assert check_exits(ctx, Market.KR) == [] and qty(ctx) == 1
    assert plan(ctx)["half"] == 1 and plan(ctx)["stop"] == 100.0


def test_fixed_style_sells_everything_at_take(ctx):
    ctx.store.save_plan("paper", "KR", "005930", "r1", 100.0, 95.0, 110.0)  # 예전 방식 / 직접 산 종목
    ctx.px["v"] = 105.0
    assert check_exits(ctx, Market.KR) == [] and qty(ctx) == 10
    ctx.px["v"] = 110.0
    assert [x["reason"] for x in check_exits(ctx, Market.KR)] == ["take"] and qty(ctx) == 0


def test_signal_buy_makes_trail_plan(ctx, monkeypatch):
    """AI 매수(신호·회의)가 체결되면 계획이 'trail' 로 만들어진다 (R = 진입가 - 손절가, 기간 10거래일)."""
    from qtrader.engine import remember_plan, trade_date
    from qtrader.storage import now
    today = trade_date(Market.KR)
    ctx.store.save_ai_run({"id": "s1", "symbol": "000660", "market": "KR", "trade_date": today, "started_at": now(),
                           "finished_at": now(), "status": "done", "origin": "signal",
                           "final": {"rating": "Overweight", "confidence": 0.7},
                           "plan": {"entry": 200.0, "stop": 190.0, "take": 220.0, "horizon_days": 20}})
    remember_plan(ctx.store, "paper", "KR", OrderRequest(Market.KR, "000660", Side.BUY, 3, 200.0, "ai"), today)
    p = [x for x in ctx.store.active_plans("paper", "KR") if x["symbol"] == "000660"][0]
    assert p["style"] == "trail" and p["risk"] == 10.0 and p["horizon"] == 10 and p["high"] == 200.0
    remember_plan(ctx.store, "paper", "KR", OrderRequest(Market.KR, "000660", Side.BUY, 3, 200.0, "ai"), today, "fixed")
    p = [x for x in ctx.store.active_plans("paper", "KR") if x["symbol"] == "000660"][0]
    assert p["style"] is None and p["horizon"] is None


def test_toss_guard_follows_moved_stop(ctx, monkeypatch):
    """토스 서버 보호(OCO)는 계획의 손절가·수량이 바뀌면 다시 건다 (본전·따라 올라가는 손절이 PC 꺼져도 지켜지게)."""
    from qtrader import guard
    calls = []
    monkeypatch.setattr(guard, "enabled", lambda c: True)
    monkeypatch.setattr(guard, "record_fired", lambda c: [])
    monkeypatch.setattr(guard, "protect", lambda c, m, s, q, st, tk: calls.append((s, q, st, tk)) or
                        guard._save(c.store, guard._load(c.store) | {f"{m.value}:{s}": {"id": "g", "qty": q, "stop": st, "take": tk}}))
    monkeypatch.setattr(guard, "release", lambda c, m, s: None)
    ctx.px["v"] = 101.0
    guard.sync(ctx)
    assert calls[-1] == ("005930", 10, 95.0, 110.0)
    ctx.px["v"] = 105.0
    check_exits(ctx, Market.KR)
    guard.sync(ctx)
    assert calls[-1] == ("005930", 5, 100.0, 130.0)
    assert json.loads(ctx.store.get(guard.KEY))["KR:005930"]["qty"] == 5
