"""신호 검증(지난 시세): 실시간 확인(signals.check)과 같은 규칙인지, 팔기 규칙 흉내, 화면 API."""
import numpy as np
import pandas as pd

from qtrader import signal_test, signals
from qtrader.data import synthetic
from qtrader.models import Market


def test_backtest_uses_exactly_the_live_rule():
    """하루씩 잘라 실시간 check 를 부른 결과와, 지표를 한 번에 계산한 검증 결과가 날마다 같아야 한다."""
    b = next(x for x in (synthetic("X", days=600, start_price=50000, seed=k) for k in range(1, 60))
             if signal_test.simulate(x, Market.KR, "X"))
    hits = {str(b.index[i])[:10]: signals.check(b.iloc[: i + 1], float(b["close"].iloc[i]), str(b.index[i])[:10], 1.0)
            for i in range(200, len(b) - 1)}
    live = {d for d, s in hits.items() if s}
    trades = signal_test.simulate(b, Market.KR, "X")
    assert trades and {t["entry_date"] for t in trades} <= live  # 검증이 산 날은 모두 실시간 규칙도 신호를 냈다
    t = trades[0]
    assert hits[t["entry_date"]]["name"] == t["kind"]


def test_exit_simulation_half_trail_and_costs():
    """+1R 절반 → 본전 → 따라 올라가는 손절. 비용까지 빼서 거래 수익률을 낸다."""
    days = pd.bdate_range(end="2026-10-27", periods=230)
    close = np.r_[np.linspace(100, 150, 210), [150, 152, 158, 165, 170, 168, 160, 150, 140, 135, 130, 128, 126, 125, 124, 123, 122, 121, 120, 119]]
    b = pd.DataFrame({"open": close, "high": close * 1.002, "low": close * 0.998, "close": close, "volume": 1e6}, index=days)
    monkey = signals.rule
    calls = {"n": 0}

    def once(price, *a, **k):  # 210번째 날 한 번만 신호
        calls["n"] += 1
        return {"kind": "pullback", "name": "눌림목 반등", "stop_pct": 4.0} if calls["n"] == 11 else None
    signal_test.rule = once
    try:
        tr = signal_test.simulate(b, Market.KR, "T")
    finally:
        signal_test.rule = monkey
    assert len(tr) == 1
    t = tr[0]
    entry = t["entry"]
    assert t["half"] and t["how"] == "본전·따라 올린 손절" and 0 < t["ret"] < 0.2 and t["r"] > 0.5, (t, entry)


def test_summary_and_api(isolated_home, monkeypatch):
    from fastapi.testclient import TestClient
    from qtrader.web.app import create_app
    app = create_app()
    c = TestClient(app)
    tok = app.state.token
    monkeypatch.setattr(signal_test, "run", lambda data, m, syms, days=750, weight=0.2: {
        "summary": {"trades": 3, "win_rate": 0.66}, "by_kind": {}, "curve": [], "symbols": len(syms), "skipped": [], "trades": [], "weight": weight})
    r = c.post("/api/signal-test", json={"market": "KR"}, headers={"x-qtrader-token": tok})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["summary"]["trades"] == 3 and d["symbols"] > 0 and d["weight"] == 0.2
    s = signal_test.summarize([{"ret": 0.1, "r": 1.0, "days": 3, "half": True, "exit_date": "2026-01-02"},
                               {"ret": -0.05, "r": -1.0, "days": 2, "half": False, "exit_date": "2026-01-03"}])
    assert s["win_rate"] == 0.5 and s["profit_factor"] == 2.0 and s["max_drawdown"] < 0
