"""KIS 클라이언트는 가짜 HTTP 세션으로, 웹은 TestClient 로 검사한다. 실제 증권사에 접속하지 않는다."""
import json

import pytest
from fastapi.testclient import TestClient

from qtrader import config
from qtrader.data import write_demo_csv
from qtrader.kis import KIS
from qtrader.models import Market, OrderRequest, Side
from qtrader.web.app import create_app


class FakeResp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.headers, self.text = body, status, {}, json.dumps(body)

    def json(self):
        return self.body


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, headers=None, json=None, data=None, timeout=None):
        self.calls.append(("POST", url, headers, json or data))
        if url.endswith("/oauth2/tokenP"):
            return FakeResp({"access_token": "TKN", "expires_in": 86400})
        return FakeResp({"rt_cd": "0", "msg1": "주문 전송 완료", "output": {"ODNO": "0001"}})

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, headers, params))
        if "inquire-daily-itemchartprice" in url:
            return FakeResp({"rt_cd": "0", "output2": [
                {"stck_bsop_date": "20261026", "stck_oprc": "100", "stck_hgpr": "110", "stck_lwpr": "95",
                 "stck_clpr": "105", "acml_vol": "1000"},
                {"stck_bsop_date": "20261023", "stck_oprc": "98", "stck_hgpr": "101", "stck_lwpr": "97",
                 "stck_clpr": "100", "acml_vol": "900"}]})
        if "quotations/price" in url:
            return FakeResp({"rt_cd": "0", "output": {"last": "201.5"}})
        return FakeResp({"rt_cd": "1", "msg_cd": "E1", "msg1": "모르는 요청"})


@pytest.fixture
def kis(tmp_path):
    s = FakeSession()
    return KIS("demo", "APPKEY123456", "SECRET123456", "12345678-01", tmp_path, session=s), s


def test_token_cached_to_file(kis, tmp_path):
    k, s = kis
    assert k.token() == "TKN"
    k2 = KIS("demo", "APPKEY123456", "SECRET123456", "12345678-01", tmp_path, session=s)
    assert k2.token() == "TKN"
    assert sum(1 for c in s.calls if c[1].endswith("tokenP")) == 1


def test_domestic_order_uses_demo_tr_id(kis):
    k, s = kis
    r = k.order(OrderRequest(Market.KR, "005930", Side.BUY, 3, 0))
    assert r.ok and r.broker_order_id == "0001"
    _, url, h, body = s.calls[-1]
    assert url.endswith("/uapi/domestic-stock/v1/trading/order-cash") and h["tr_id"] == "VTTC0012U"
    body = json.loads(body)
    assert body["ORD_DVSN"] == "01" and body["ORD_QTY"] == "3" and body["CANO"] == "12345678"


def test_us_sell_order_demo_tr_id(kis):
    k, s = kis
    k.order(OrderRequest(Market.US, "NYSE:KO", Side.SELL, 2, 61.234))
    _, url, h, body = s.calls[-1]
    body = json.loads(body)
    assert h["tr_id"] == "VTTT1001U" and body["OVRS_EXCG_CD"] == "NYSE" and body["PDNO"] == "KO"
    assert body["OVRS_ORD_UNPR"] == "61.23" and body["ORD_DVSN"] == "00"


def test_daily_bars_and_price(kis):
    k, _ = kis
    df = k.daily_bars(Market.KR, "005930", 2)
    assert list(df["close"]) == [100.0, 105.0] and df.index.is_monotonic_increasing
    assert k.price(Market.US, "AAPL") == 201.5


def test_error_is_reported(kis):
    k, _ = kis
    r = k.order(OrderRequest(Market.KR, "005930", Side.BUY, 1, 0))
    assert r.ok
    with pytest.raises(Exception, match="모르는 요청"):
        k.account(Market.KR)


def test_bad_account_format(tmp_path):
    with pytest.raises(ValueError):
        KIS("demo", "k", "s", "1234-01", tmp_path)


@pytest.fixture
def client(isolated_home):
    write_demo_csv(isolated_home / "data", config.load())
    app = create_app()
    c = TestClient(app)
    c.token = app.state.token
    return c


def test_web_status_and_index(client):
    assert client.token in client.get("/").text
    s = client.get("/api/status").json()
    assert s["mode"] == "paper" and s["data_source"] == "csv" and not s["auto_trading"]


def test_web_mutations_need_token(client):
    assert client.post("/api/kill-switch", json={"on": True}).status_code == 403
    r = client.post("/api/kill-switch", json={"on": True}, headers={"x-qtrader-token": client.token})
    assert r.status_code == 200 and client.get("/api/status").json()["kill_switch"]


def test_web_rejects_foreign_host(client):
    assert client.get("/api/status", headers={"host": "evil.example.com"}).status_code == 403


def test_web_backtest_and_run(client):
    h = {"x-qtrader-token": client.token}
    r = client.post("/api/backtest", json={"market": "KR", "strategy": "trend", "days": 400}, headers=h)
    assert r.status_code == 200 and len(r.json()["equity"]) > 300
    r = client.post("/api/run-once", json={"market": "KR", "dry_run": True}, headers=h).json()
    assert r["dry_run"] and all(o["status"] == "dry_run" for o in r["orders"])


def test_web_config_validation(client):
    h = {"x-qtrader-token": client.token}
    cfg = client.get("/api/config").json()
    cfg["risk"]["max_weight"] = 5
    assert client.put("/api/config", json=cfg, headers=h).status_code == 400
    cfg["risk"]["max_weight"] = 0.25
    assert client.put("/api/config", json=cfg, headers=h).json()["risk"]["max_weight"] == 0.25
