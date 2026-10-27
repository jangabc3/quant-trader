"""실전 주문 PIN, 시장 국면 규칙, 투자 성향 검사 전 자동매매 막기."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qtrader import pinlock, principles
from qtrader.web.app import create_app


@pytest.fixture(autouse=True)
def fresh():
    pinlock.reset_state()
    yield
    pinlock.reset_state()


def test_pin_format_and_lockout(tmp_path):
    with pytest.raises(pinlock.PinError, match="4~8"):
        pinlock.set_pin(tmp_path, "12")
    with pytest.raises(pinlock.PinError, match="연속"):
        pinlock.set_pin(tmp_path, "1234")
    pinlock.set_pin(tmp_path, "482916")
    assert "482916" not in (tmp_path / "pin.json").read_text()
    pinlock.verify(tmp_path, "482916")
    pinlock.verify(tmp_path, "")  # 5분 안에는 다시 묻지 않음
    pinlock.lock_now()
    for _ in range(4):
        with pytest.raises(pinlock.PinError, match="틀렸"):
            pinlock.verify(tmp_path, "000111")
    with pytest.raises(pinlock.PinError, match="잠겼"):
        pinlock.verify(tmp_path, "000111")
    with pytest.raises(pinlock.PinError, match="잠겼"):
        pinlock.verify(tmp_path, "482916")
    with pytest.raises(pinlock.PinError):  # 바꿀 때는 지금 PIN 필요
        pinlock.set_pin(tmp_path, "735102", "000000")


@pytest.fixture
def client(isolated_home):
    app = create_app()
    c = TestClient(app)
    c.h = {"x-qtrader-token": app.state.token}
    return c


def test_live_mode_needs_pin(client):
    cfg = client.get("/api/config").json()
    cfg["mode"], cfg["live_confirmed"] = "toss_real", True
    r = client.put("/api/config", json=cfg, headers=client.h)
    assert r.status_code == 423 and "PIN 을 먼저" in r.json()["detail"]
    assert client.post("/api/pin", json={"new": "482916"}, headers=client.h).json()["set"]
    pinlock.lock_now()
    assert client.put("/api/config", json=cfg, headers=client.h).status_code == 428
    assert client.put("/api/config", json=cfg, headers=client.h | {"x-qtrader-pin": "999888"}).status_code == 428
    ok = client.put("/api/config", json=cfg, headers=client.h | {"x-qtrader-pin": "482916"})
    assert ok.status_code == 200 and ok.json()["mode"] == "toss_real"


def test_auto_needs_profile(client):
    r = client.post("/api/auto", json={"on": True}, headers=client.h)
    assert r.status_code == 400 and "투자 성향" in r.json()["detail"]


def bars(trend):
    c = 100 * np.exp(np.cumsum(np.full(300, trend)))
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "volume": 1}, index=pd.date_range("2025-01-01", periods=300))


def test_market_regime_halves_or_blocks():
    final = {"rating": "Buy", "confidence": 0.8, "stop_loss_pct": 5, "take_profit_pct": 15}
    check = {"fact_checks": [{"status": "확인됨"}]}
    up, down = bars(0.002), bars(-0.002)
    base = principles.checklist(final, check, up, rules={"max_rsi": 101}, market_bars=up)
    half = principles.checklist(final, check, up, rules={"max_rsi": 101}, market_bars=down)
    assert base["passed"] and half["passed"]
    assert half["size"]["weight_factor"] == pytest.approx(base["size"]["weight_factor"] / 2, abs=1e-3)
    block = principles.checklist(final, check, up, rules={"max_rsi": 101, "market_filter": "block"}, market_bars=down)
    assert not block["passed"] and any(r["key"] == "regime" and not r["ok"] for r in block["rules"])
