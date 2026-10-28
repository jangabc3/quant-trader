"""종목 화면 데이터·뉴스·재무제표. 실제 외부 서버에 접속하지 않는다."""
import io
import zipfile

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qtrader import config, fundamentals, news, stockdata
from qtrader.web.app import create_app

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>
<item><title>코스피, 외국인 매수에 상승 &amp; 마감</title><link>https://kr.investing.com/news/stock-market-news/a-1</link>
<pubDate>Mon, 26 Oct 2026 07:10:00 GMT</pubDate><description>&lt;p&gt;요약 본문&lt;/p&gt;</description></item>
<item><title>나스닥 하락</title><link>https://kr.investing.com/news/stock-market-news/b-2</link>
<pubDate>Mon, 26 Oct 2026 09:00:00 GMT</pubDate></item>
<item><title>나쁜 링크</title><link>javascript:alert(1)</link></item>
</channel></rss>""".encode()


class Resp:
    def __init__(self, content=b"", body=None, status=200):
        self.content, self.body, self.status_code = content, body, status

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def test_rss_parse_safe_and_sorted():
    items = news.parse(RSS, "테스트")
    assert [x["title"] for x in items] == ["코스피, 외국인 매수에 상승 & 마감", "나스닥 하락"]  # javascript: 링크 제외
    assert items[0]["summary"] == "요약 본문" and items[0]["time"].startswith("2026-10-26")

    class S:
        def get(self, url, headers=None, timeout=None):
            return Resp(RSS)
    out = news.feeds([{"name": "a", "url": "https://x.test/1"}, {"name": "b", "url": "https://x.test/2"}], session=S())
    assert [x["title"] for x in out["items"]] == ["나스닥 하락", "코스피, 외국인 매수에 상승 & 마감"]  # 중복 링크 제거, 최신순


def test_rss_rejects_entities_bomb():
    bomb = b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]><rss><channel><item><title>&lol2;</title></item></channel></rss>'
    with pytest.raises(Exception):
        news.parse(bomb, "x")


def test_search_and_resample():
    rows = [{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}, {"symbol": "005935", "name": "삼성전자우", "market": "KOSPI"},
            {"symbol": "028260", "name": "삼성물산", "market": "KOSPI"}, {"symbol": "AAPL", "name": "애플", "market": "NASDAQ"}]
    assert [r["symbol"] for r in stockdata.search(rows, "삼성전자")] == ["005930", "005935"]
    assert stockdata.search(rows, "aapl")[0]["name"] == "애플" and stockdata.search(rows, "") == []
    idx = pd.date_range("2026-10-26 09:00", periods=10, freq="min")
    df = pd.DataFrame({"open": range(10), "high": range(1, 11), "low": range(10), "close": range(10), "volume": 1.0}, index=idx)
    r = stockdata._resample(df, "5min")
    assert len(r) == 2 and r["open"].iloc[1] == 5 and r["high"].iloc[1] == 10 and r["volume"].iloc[0] == 5


@pytest.mark.parametrize("iv", list(stockdata.INTERVALS))
def test_demo_candles_all_intervals(iv):
    c = stockdata.candles(None, "005930", iv)
    assert c["demo"] and len(c["candles"]) > 10
    times = [x["time"] for x in c["candles"]]
    assert times == sorted(times) and c["intraday"] == iv.endswith("m")


def test_dart_parsing(tmp_path):
    corp = '<?xml version="1.0" encoding="UTF-8"?><result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list></result>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", corp)
    rows = [
        {"sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "300", "frmtrm_amount": "250", "bfefrmtrm_amount": "200"},
        {"sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "30", "frmtrm_amount": "20", "bfefrmtrm_amount": "10"},
        {"sj_div": "IS", "account_nm": "당기순이익", "thstrm_amount": "24", "frmtrm_amount": "15", "bfefrmtrm_amount": "8"},
        {"sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "100", "frmtrm_amount": "90", "bfefrmtrm_amount": "80"},
        {"sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "200", "frmtrm_amount": "180", "bfefrmtrm_amount": "160"},
    ]
    calls = []

    class S:
        def get(self, url, params=None, timeout=None):
            calls.append((url, params))
            if url.endswith("corpCode.xml"):
                return Resp(buf.getvalue())
            return Resp(body={"status": "000", "list": rows})
    fin = fundamentals.dart_financials("005930", "k" * 40, tmp_path, session=S())
    assert fin["series"]["revenue"] == [200, 250, 300] and fin["currency"] == "KRW"
    assert calls[1][1]["corp_code"] == "00126380" and calls[1][1]["reprt_code"] == "11011"
    r = fundamentals.ratios(fin, price=100, shares=3)
    assert r["operating_margin"] == pytest.approx(0.1) and r["roe"] == pytest.approx(0.12)
    assert r["revenue_growth"] == pytest.approx(0.2) and r["per"] == pytest.approx(12.5) and r["pbr"] == pytest.approx(1.5)


def test_sec_needs_contact_and_parses():
    with pytest.raises(fundamentals.FundamentalsError):
        fundamentals.sec_financials("AAPL", "")
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [{"form": "10-K", "fp": "FY", "fy": 2024, "val": 100}, {"form": "10-K", "fp": "FY", "fy": 2025, "val": 120},
                                       {"form": "10-Q", "fp": "Q1", "fy": 2026, "val": 40}]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [{"form": "10-K", "fp": "FY", "fy": 2025, "val": 6.0}]}}}}}
    seen = []

    class S:
        def get(self, url, headers=None, timeout=None):
            seen.append(headers["User-Agent"])
            if "company_tickers" in url:
                return Resp(body={"0": {"ticker": "AAPL", "cik_str": 320193}})
            assert "CIK0000320193" in url
            return Resp(body=facts)
    fin = fundamentals.sec_financials("AAPL", "me@example.com", session=S())
    assert fin["years"] == [2024, 2025] and fin["series"]["revenue"] == [100, 120] and fin["series"]["eps"] == [None, 6.0]
    assert "me@example.com" in seen[0]
    assert fundamentals.ratios(fin, price=180)["per"] == pytest.approx(30)


@pytest.fixture
def client(isolated_home):
    app = create_app()
    c = TestClient(app)
    c.h = {"x-qtrader-token": app.state.token}
    return c


def test_stock_endpoints_demo(client):
    assert client.get("/api/stocks/search?q=삼성").json()[0]["symbol"] == "005930"
    s = client.get("/api/stock/005930/summary").json()
    assert s["demo"] and s["price"] > 0 and s["name"] == "삼성전자"
    c = client.get("/api/stock/005930/candles?interval=1w").json()
    assert c["interval"] == "1w" and c["candles"]
    assert client.get("/api/stock/005930/candles?interval=3h").status_code == 400
    ob = client.get("/api/stock/005930/orderbook").json()
    assert len(ob["asks"]) == 10 and ob["asks"][0]["price"] > ob["bids"][0]["price"]
    assert client.get("/api/stock/005930/trades").json()["items"]
    assert "DART" in client.get("/api/stock/005930/financials").json()["error"]
    assert "연락처" in client.get("/api/stock/AAPL/financials").json()["error"]
    p = client.get("/api/principles").json()
    assert len(p["references"]) >= 15 and p["rules"]["min_reward_risk"] == 2.0


def test_dart_key_endpoint(client, fake_vault):
    r = client.put("/api/secrets", json={"kind": "dart", "key": "d" * 40}, headers=client.h).json()
    assert r["keys"]["dart"] and fake_vault["dart_api_key"] == "d" * 40


def test_sector_and_account_endpoints(client, isolated_home):
    from qtrader.data import write_demo_csv
    write_demo_csv(isolated_home / "data", config.load())
    sec = client.get("/api/market/sectors").json()
    assert sec["demo"] and sec["items"][0]["changeRate"] >= sec["items"][-1]["changeRate"]
    sid = sec["items"][0]["sectorId"]
    assert client.get(f"/api/sector/{sid}/stocks").json()["items"]
    assert client.get("/api/stock/005930/extra").json() == {"program": [], "short": [], "credit": [], "lending": []}
    ov = client.get("/api/account/overview").json()
    kr = ov["markets"]["KR"]
    assert ov["broker"] == "paper" and kr["equity"] == kr["cash"] == 10_000_000 and kr["positions"] == 0
    assert client.get("/api/account/open-orders").json()["items"] == []
    assert client.post("/api/account/orders/x/cancel", headers=client.h).status_code == 400


def test_index_has_build_id(client):
    html = client.get("/").text
    assert "__BUILD__" not in html and "/static/chat.js?v=" in html


def test_investor_profile(client):
    m = client.get("/api/profile").json()
    assert m["investor"] is None and len(m["questions"]) == 8 and len(m["types"]) == 5
    safe = {"age": 4, "exp": 0, "goal": 0, "horizon": 0, "loss": 0, "share": 0, "drop": 0, "style": 0}
    bold = {"age": 0, "exp": 4, "goal": 4, "horizon": 4, "loss": 4, "share": 3, "drop": 3, "style": 1}
    assert client.post("/api/profile", json={"answers": safe}).status_code == 403
    r = client.post("/api/profile", json={"answers": safe}, headers=client.h).json()
    assert r["type"] == "안정형" and r["style"] == "분산형"
    cfg = client.get("/api/config").json()
    assert cfg["risk"]["max_weight"] == 0.10 and cfg["ai"]["rules"]["min_confidence"] == 0.75 and "안정형" in cfg["ai"]["profile"]
    r = client.post("/api/profile", json={"answers": bold}, headers=client.h).json()
    assert r["type"] == "공격투자형" and r["style"] == "집중형"
    cfg = client.get("/api/config").json()
    assert cfg["risk"]["max_weight"] == 0.30 and cfg["risk"]["daily_loss_limit"] == 0.04
    assert any(c["k"] == "손절·익절" for c in r["criteria"])
    assert client.post("/api/profile", json={"answers": {"age": 1}}, headers=client.h).status_code == 400


def test_search_understands_partial_names_initials_and_aliases():
    """'하이닉'·'sk 하이'·'하닉'·초성·영어 이름으로도 찾는다."""
    from qtrader import stockdata as sd
    rows = [{"symbol": "000660", "name": "SK하이닉스", "type": "STOCK"}, {"symbol": "005930", "name": "삼성전자", "type": "STOCK"},
            {"symbol": "005935", "name": "삼성전자우", "type": "STOCK"}, {"symbol": "034730", "name": "SK", "type": "STOCK"},
            {"symbol": "091160", "name": "KODEX 반도체", "type": "ETF"}, {"symbol": "AAPL", "name": "애플", "type": "STOCK"},
            {"symbol": "NVDA", "name": "엔비디아", "type": "STOCK"}]
    top = lambda q: (sd.search(rows, q, 3) or [{}])[0].get("name")
    for q in ("하이닉", "sk 하이", "SK하이", "하닉"):
        assert top(q) == "SK하이닉스", q
    assert top("ㅅㅅㅈㅈ") == top("삼전") == top("삼성") == "삼성전자"
    assert top("apple") == top("aapl") == "애플" and top("nvidia") == top("엔비") == "엔비디아"
    assert top("sk") == "SK" and top("반도체") == "KODEX 반도체" and sd.search(rows, "없는회사") == []
    assert sd.resolve(rows, "SK하이닉") == "000660" and sd.resolve(rows, "005930") == "005930" and sd.resolve(rows, "없는회사") is None


def test_meeting_by_name_uses_stock_code(isolated_home):
    """회의 입력칸에 이름을 써도 종목 코드로 바꿔 회의한다 (이름 그대로 토스에 보내던 400 오류)."""
    from qtrader import config
    from qtrader.data import write_demo_csv
    from test_toss_ai import FakeLLM
    write_demo_csv(isolated_home / "data", config.load())
    app = create_app(llm=FakeLLM())
    client = TestClient(app)
    client.h = {"x-qtrader-token": app.state.token}
    r = client.post("/api/ai/meeting", json={"symbol": "SK하이닉"}, headers=client.h)
    assert r.status_code == 200 and r.json()["symbol"] == "000660"
    bad = client.post("/api/ai/meeting", json={"symbol": "없는회사이름"}, headers=client.h)
    assert bad.status_code == 400 and "찾지 못했어요" in bad.json()["detail"]


def test_change_rate_uses_toss_base_price_everywhere():
    """등락률은 토스가 준 기준가(전일 정규장 종가)로. 일봉 두 개로 계산하면 시간외가 섞인 날 순위표와 어긋났다."""
    import pandas as pd
    from qtrader import market as market_mod, stockdata
    from qtrader.toss import Toss

    class T(Toss):
        def __init__(self):
            pass

        def _call(self, method, path, group, params=None, **kw):
            assert path == "/api/v1/prices"
            return [{"symbol": "005930", "lastPrice": "239250", "changeRate": "0.0788"}]

        def candles(self, symbol, interval="1d", count=200, indicator=False):  # 어제 일봉 종가는 시간외까지 섞인 224,500
            idx = pd.to_datetime(["2026-10-27", "2026-10-28"])
            return pd.DataFrame({"open": [1, 1], "high": [240000, 240000], "low": [1, 1], "close": [224500.0, 239250.0],
                                 "volume": [1, 1]}, index=idx)
    t = T()
    q = t.quotes(["005930"])["005930"]
    assert q["change_rate"] == pytest.approx(0.0788) and q["base"] == pytest.approx(239250 / 1.0788)
    d = market_mod._change(t, "005930", False)
    assert d["change_rate"] == pytest.approx(0.0788, abs=1e-6)  # 관심 종목·시장 요약
    t.stocks = lambda s: [{"symbol": "005930", "name": "삼성전자"}]
    t.warnings = lambda s: []
    t.price_limits = lambda s: {}
    monkey = stockdata.candles
    stockdata.candles = lambda tt, sym, iv, n: {"candles": [{"close": 224500.0, "volume": 1}, {"close": 239250.0, "volume": 1}]}
    try:
        s = stockdata.summary(t, "005930")
    finally:
        stockdata.candles = monkey
    assert s["change_rate"] == pytest.approx(0.0788, abs=1e-6)  # 종목 화면
