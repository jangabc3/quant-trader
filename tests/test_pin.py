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


def test_manual_order_paper(client, isolated_home):
    from qtrader import config
    from qtrader.data import write_demo_csv
    write_demo_csv(isolated_home / "data", config.load())
    info = client.get("/api/order/info/005930").json()
    assert info["price"] > 0 and info["cash"] > 0 and not info["live"] and info["sellable"] == 0
    bad = client.post("/api/order", json={"symbol": "005930", "side": "sell", "qty": 1}, headers=client.h)
    assert bad.status_code == 400  # 없는 주식은 못 판다
    r = client.post("/api/order", json={"symbol": "005930", "side": "buy", "qty": 3}, headers=client.h).json()
    assert r["status"] == "filled"
    assert client.get("/api/order/info/005930").json()["sellable"] == 3
    big = client.post("/api/order", json={"symbol": "005930", "side": "buy", "qty": 10**6}, headers=client.h)
    assert big.status_code == 400 and "한도" in big.json()["detail"]
    assert client.get("/api/trades/why").json()[0]["summary"] == "직접 주문"


def test_pin_switch_off_needs_pin_then_skips(client):
    assert client.post("/api/pin", json={"new": "482916"}, headers=client.h).json()["set"]
    pinlock.lock_now()
    cfg = client.get("/api/config").json()
    cfg["security"] = {"pin": False}
    assert client.put("/api/config", json=cfg, headers=client.h).status_code == 428  # 끄려면 PIN 필요
    assert client.put("/api/config", json=cfg, headers=client.h | {"x-qtrader-pin": "482916"}).status_code == 200
    pinlock.lock_now()
    cfg = client.get("/api/config").json()
    cfg["mode"], cfg["live_confirmed"] = "toss_real", True
    assert client.put("/api/config", json=cfg, headers=client.h).status_code == 200  # 꺼져 있으면 묻지 않는다
    assert client.get("/api/pin").json()["enabled"] is False
