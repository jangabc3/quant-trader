"""토스증권 클라이언트(가짜 HTTP), AI 회의(가짜 LLM), AI 등급 -> 주문, 시장 화면 API."""
import json
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from qtrader import config
from qtrader.agents import meeting
from qtrader.agents.llm import Reply
from qtrader.agents.roles import ROLES
from qtrader.data import synthetic, write_demo_csv
from qtrader.engine import ai_targets, build_context, run_market
from qtrader.models import Market, OrderRequest, Side
from qtrader.storage import Store
from qtrader.toss import Toss, TossError
from qtrader.web.app import create_app


class Resp:
    def __init__(self, body, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}
        self.text = json.dumps(body)

    def json(self):
        return self.body


class FakeToss:
    """openapi.json 예시 응답 모양을 따라 만든 가짜 서버."""

    def __init__(self):
        self.calls, self.tokens, self.fail_once_429 = [], 0, False

    def post(self, url, data=None, timeout=None):
        assert url.endswith("/oauth2/token") and data["grant_type"] == "client_credentials"
        self.tokens += 1
        return Resp({"access_token": f"T{self.tokens}", "token_type": "Bearer", "expires_in": 86400})

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append((method, url, params, json, headers))
        path = url.split("tossinvest.com")[-1]
        if self.fail_once_429:
            self.fail_once_429 = False
            return Resp({"error": {"requestId": "r", "code": "rate-limit-exceeded", "message": ""}}, 429,
                        {"Retry-After": "0"})
        if path == "/api/v1/accounts":
            return Resp({"result": [{"accountNo": "123", "accountSeq": 7, "accountType": "BROKERAGE"}]})
        if path == "/api/v1/holdings":
            return Resp({"result": {"items": [
                {"symbol": "005930", "marketCountry": "KR", "quantity": "10", "lastPrice": "72000",
                 "averagePurchasePrice": "65000"},
                {"symbol": "AAPL", "marketCountry": "US", "quantity": "3", "lastPrice": "180", "averagePurchasePrice": "150"}]}})
        if path == "/api/v1/buying-power":
            return Resp({"result": {"currency": params["currency"], "cashBuyingPower": "5000000" if params["currency"] == "KRW" else "900.5"}})
        if path == "/api/v1/prices":
            return Resp({"result": [{"symbol": s, "lastPrice": "100"} for s in params["symbols"].split(",")]})
        if path == "/api/v1/candles":
            before = params.get("before")
            if before is None:
                return Resp({"result": {"candles": [
                    {"timestamp": "2026-03-25T00:00:00+09:00", "openPrice": "1", "highPrice": "2", "lowPrice": "1", "closePrice": "72000", "volume": "5"},
                    {"timestamp": "2026-03-24T00:00:00+09:00", "openPrice": "1", "highPrice": "2", "lowPrice": "1", "closePrice": "71600", "volume": "5"}],
                    "nextBefore": "2026-03-23T00:00:00+09:00"}})
            return Resp({"result": {"candles": [
                {"timestamp": "2026-03-23T00:00:00+09:00", "openPrice": "1", "highPrice": "2", "lowPrice": "1", "closePrice": "71000", "volume": "5"}],
                "nextBefore": None}})
        if path == "/api/v1/orders" and method == "POST":
            if json["symbol"] == "BAD":
                return Resp({"error": {"requestId": "r", "code": "invalid-request", "message": "호가 단위 오류"}}, 400)
            return Resp({"result": {"orderId": "OID1", "clientOrderId": json.get("clientOrderId")}})
        if path == "/api/v1/market-calendar/KR":
            return Resp({"result": {"today": {"date": "2026-05-05", "integrated": None}, "previousBusinessDay": {
                "date": "2026-05-04", "integrated": {"regularMarket": {"startTime": "2026-05-04T09:00:00+09:00",
                                                                        "endTime": "2026-05-04T15:30:00+09:00"}}}}})
        return Resp({"error": {"requestId": "r", "code": "not-found", "message": "없음"}}, 404)


@pytest.fixture
def toss(tmp_path):
    s = FakeToss()
    return Toss("tsck_live_client123", "secret-xxxxxxxx", tmp_path, session=s), s


def test_toss_account_and_header(toss):
    t, s = toss
    a = t.account(Market.KR)
    assert a.cash == 5_000_000 and a.positions["005930"].qty == 10 and "AAPL" not in a.positions
    hold = [c for c in s.calls if c[1].endswith("/holdings")][0]
    assert hold[4]["X-Tossinvest-Account"] == "7" and hold[4]["Authorization"] == "Bearer T1"
    assert t.account(Market.US).cash == 900.5


def test_toss_token_shared_via_file(toss, tmp_path):
    t, s = toss
    t.token()
    t2 = Toss("tsck_live_client123", "secret-xxxxxxxx", tmp_path, session=s)
    t2.token()
    assert s.tokens == 1  # 재발급하면 이전 토큰이 무효가 되므로 한 번만 받아야 한다


def test_toss_candles_paginate_oldest_first(toss):
    t, _ = toss
    df = t.candles("005930", "1d", 3)
    assert list(df["close"]) == [71000, 71600, 72000] and df.index.is_monotonic_increasing


def test_toss_order_body_and_errors(toss):
    t, s = toss
    r = t.order(OrderRequest(Market.US, "NYSE:KO", Side.BUY, 4, 61.239), client_order_id="qt-1", loc=True)
    assert r.ok and r.broker_order_id == "OID1"
    body = s.calls[-1][3]
    assert body == {"symbol": "KO", "side": "BUY", "quantity": "4", "orderType": "LIMIT", "price": "61.24",
                    "timeInForce": "CLS", "clientOrderId": "qt-1"}
    t.order(OrderRequest(Market.KR, "005930", Side.SELL, 2, 0))
    assert s.calls[-1][3]["orderType"] == "MARKET" and "price" not in s.calls[-1][3]
    bad = t.order(OrderRequest(Market.KR, "BAD", Side.BUY, 1, 70050))
    assert not bad.ok and "호가 단위" in bad.message


def test_toss_retries_429(toss):
    t, s = toss
    s.fail_once_429 = True
    assert t.prices(["005930"]) == {"005930": 100.0}


def test_toss_holiday(toss):
    t, _ = toss
    from datetime import datetime
    from qtrader.toss import KST
    assert not t.in_regular_session(Market.KR, datetime(2026, 5, 5, 10, 0, tzinfo=KST))
    with pytest.raises(TossError):
        t.stock_investor_trading("005930")


# AI 회의
FINAL = {"rating": "Buy", "confidence": 0.8, "headline": "추세 살아있음", "summary": ["a", "b", "c"],
         "thesis": "실적 개선", "reasons": ["외국인 순매수", "실적", "수주"],
         "short_args": [{"claim": "고평가", "rebuttal": "성장률 감안"}] * 3,
         "scenarios": {"bear": {"price": 80, "probability": 0.2, "condition": "수요 둔화"},
                       "base": {"price": 110, "probability": 0.5, "condition": "예상대로"},
                       "bull": {"price": 140, "probability": 0.3, "condition": "수주 확대"}},
         "entry_low": 95, "entry_high": 100, "stop_loss_pct": 7, "take_profit_pct": 15, "horizon_days": 20,
         "trust": ["t1", "t2", "t3"], "worry": ["w1", "w2", "w3"], "watch": ["m1", "m2", "m3"],
         "risk_votes": {"aggr": "확대", "neut": "유지", "cons": "축소"}, "bull_score": 64}
CHECK = {"fact_checks": [{"claim": "영업이익 증가", "status": "확인됨", "detail": "2분기 보고서", "source": "DART"}],
         "key_questions": [{"question": "q", "answer": "a", "source": "s"}] * 5,
         "valuation": [{"metric": "PER", "current": 12.0, "hist_avg": 15.0, "peer_avg": None, "unit": "배", "note": ""}],
         "scores": {"technical": 70, "fundamental": 60, "valuation": 65, "earnings": 55, "news": 50, "flow": 72},
         "summary": "대체로 확인"}


class FakeLLM:
    def __init__(self, rating="Buy", confidence=0.8):
        self.rating, self.confidence, self.calls = rating, confidence, []

    def run(self, system, user, web=False, schema=None, max_tokens=16000, model=None):
        self.calls.append({"system": system, "web": web, "schema": bool(schema), "model": model})
        if schema is not None and "fact_checks" in schema["properties"]:
            return Reply(json.dumps(CHECK), data=CHECK, input_tokens=100, output_tokens=50)
        if schema:
            d = FINAL | {"rating": self.rating, "confidence": self.confidence}
            return Reply(json.dumps(d), data=d, input_tokens=100, output_tokens=50)
        return Reply("한줄요약: 테스트 보고서\n- 근거 1", sources=[{"title": "s", "url": "https://example.com"}],
                     input_tokens=100, output_tokens=50)


class Bars:
    name = "fake"

    def bars(self, market, symbol, days):
        return synthetic(symbol, days=300, seed=3)

    def price(self, market, symbol):
        return 100.0


def test_meeting_runs_tradingagents_flow(tmp_path):
    st = Store(tmp_path / "m.db")
    cfg = config.load()
    llm = FakeLLM()
    run = meeting.start("005930", Bars(), st, cfg, llm=llm, background=False)
    assert run["status"] == "done", run["error"]
    assert run["final"]["rating"] == "Buy"
    assert all(run["agents"][r.id]["status"] == "done" for r in ROLES)
    # 분석가 5 + 검증관 + 강세/약세 2 + 팀장 + 트레이더 + 리스크 3 + 대표 = 14번
    assert len(llm.calls) == 14 and sum(c["schema"] for c in llm.calls) == 2
    assert [c["web"] for c in llm.calls].count(True) == 5
    models = [c["model"] for c in llm.calls]
    assert models.count("claude-opus-5-5") == 3 and models.count("claude-sonnet-5-5") == 11
    assert "투자자 프로필" in llm.calls[0]["system"] and "출처" in llm.calls[0]["system"]
    assert run["check"]["scores"]["flow"] == 72 and run["facts"]["levels"]["sma20"]
    assert all(v == "done" for v in run["stages"].values()) and len(run["handoffs"]) >= 10
    saved = st.latest_decision("005930", run["trade_date"])
    assert saved["final"]["confidence"] == 0.8 and run["tokens_in"] == 1400
    hist = st.ai_runs(1)[0]
    assert hist["check"]["valuation"][0]["metric"] == "PER" and hist["name"] == "005930"


def test_meeting_error_is_recorded(tmp_path):
    class Boom(FakeLLM):
        def run(self, *a, **k):
            from qtrader.agents.llm import LLMError
            raise LLMError("키 없음")
    st = Store(tmp_path / "m.db")
    run = meeting.start("000660", Bars(), st, config.load(), llm=Boom(), background=False)
    assert run["status"] == "error" and "키 없음" in run["error"]
    assert st.ai_runs(5)[0]["status"] == "error"


def test_ai_targets_map_ratings(tmp_path):
    st = Store(tmp_path / "m.db")
    cfg = config.load()
    meeting.start("005930", Bars(), st, cfg, llm=FakeLLM("Buy", 0.9), background=False)
    meeting.start("000660", Bars(), st, cfg, llm=FakeLLM("Sell", 0.9), background=False)
    meeting.start("035420", Bars(), st, cfg, llm=FakeLLM("Hold", 0.9), background=False)
    meeting.start("005380", Bars(), st, cfg, llm=FakeLLM("Buy", 0.3), background=False)
    today = st.ai_runs(1)[0]["trade_date"]
    target, keep, notes = ai_targets(st, cfg, ["005930", "000660", "035420", "005380", "373220"], today)
    assert target == {"005930": pytest.approx(0.2), "000660": 0.0}
    assert keep == {"035420", "005380", "373220"}
    assert {n["symbol"]: n["action"] for n in notes}["373220"] == "회의 결과 없음"


def test_engine_ai_strategy_keeps_undecided(isolated_home):
    cfg = config.load()
    cfg["markets"]["KR"]["strategy"] = "ai"
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    ctx = build_context()
    r = run_market(ctx, Market.KR)
    assert r["strategy"] == "ai" and r["orders"] == []  # 회의가 없으면 아무것도 사고팔지 않는다
    meeting.start("005930", ctx.data, ctx.store, ctx.cfg, llm=FakeLLM("Buy", 0.9), background=False)
    r = run_market(ctx, Market.KR, force=True)
    assert [(o["symbol"], o["side"], o["status"]) for o in r["orders"]] == [("005930", "buy", "pending_approval")]
    assert ctx.broker.account(Market.KR).positions == {}  # 승인 전에는 주문이 나가지 않는다
    r2 = run_market(ctx, Market.KR, force=True)
    assert r2["orders"] == []  # 같은 날 같은 종목 승인 대기는 하나만
    pend = ctx.store.approvals("pending")
    assert len(pend) == 1
    from qtrader.engine import execute_approval
    res = execute_approval(ctx, pend[0]["id"])
    assert res["status"] == "filled" and ctx.broker.account(Market.KR).positions["005930"].qty > 0
    with pytest.raises(ValueError):
        execute_approval(ctx, pend[0]["id"])


def test_approval_blocked_by_kill_switch(isolated_home):
    cfg = config.load()
    cfg["markets"]["KR"]["strategy"] = "ai"
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    ctx = build_context()
    meeting.start("005930", ctx.data, ctx.store, ctx.cfg, llm=FakeLLM("Buy", 0.9), background=False)
    run_market(ctx, Market.KR)
    aid = ctx.store.approvals("pending")[0]["id"]
    ctx.store.set("kill_switch", "1")
    from qtrader.engine import execute_approval
    with pytest.raises(ValueError, match="킬 스위치"):
        execute_approval(ctx, aid)
    assert ctx.store.approvals()[0]["status"] == "rejected"


def test_track_counts_hits():
    from qtrader.agents import track
    import pandas as pd
    idx = pd.bdate_range("2026-01-01", periods=40)
    up = pd.DataFrame({"open": 100.0, "high": [100 + i for i in range(40)], "low": [99.0 + i for i in range(40)],
                       "close": [100.0 + i for i in range(40)], "volume": 1.0}, index=idx)
    d = idx[10].strftime("%Y-%m-%d")
    runs = [{"id": "a", "symbol": "005930", "status": "done", "trade_date": d, "final": {"rating": "Buy", "horizon_days": 5,
             "take_profit_pct": 5, "stop_loss_pct": 5}},
            {"id": "b", "symbol": "005930", "status": "done", "trade_date": d, "final": {"rating": "Sell"}},
            {"id": "c", "symbol": "005930", "status": "done", "trade_date": d, "final": {"rating": "Hold"}},
            {"id": "d", "symbol": "005930", "status": "error", "trade_date": d, "final": None}]
    out = track.evaluate(runs, lambda s: up)
    assert out["summary"]["decisions"] == 3 and out["summary"]["judged"] == 2 and out["summary"]["hit_rate"] == 0.5
    a = out["rows"][0]
    assert a["correct"] and a["hit_take"] and not a["hit_stop"] and a["matured"]


def test_key_levels():
    from qtrader.agents.context import key_levels
    df = synthetic("X", days=300, start_price=50000, seed=5)
    lv = key_levels(df)
    last = df["close"].iloc[-1]
    assert all(x < last for x in lv["support"]) and all(x > last for x in lv["resistance"])
    assert lv["low52"] <= last <= lv["high52"]


def test_toss_real_mode_needs_keys_and_confirmation(fake_vault):
    cfg = config.load()
    cfg["mode"] = "toss_real"
    with pytest.raises(RuntimeError, match="live_confirmed"):
        build_context(cfg)
    cfg["live_confirmed"] = True
    with pytest.raises(RuntimeError, match="토스증권"):
        build_context(cfg)
    fake_vault.update(toss_client_id="tsck_live_abcdefgh", toss_client_secret="secret-12345678")
    ctx = build_context(cfg)
    assert ctx.broker.name == "toss" and ctx.broker.live and ctx.data.name == "toss"


# 웹
@pytest.fixture
def client(isolated_home):
    write_demo_csv(isolated_home / "data", config.load())
    app = create_app(llm=FakeLLM())
    c = TestClient(app)
    c.h = {"x-qtrader-token": app.state.token}
    return c


def test_market_endpoints_demo(client):
    ov = client.get("/api/market/overview").json()
    assert ov["demo"] and {i["symbol"] for i in ov["indices"]} == {"KOSPI", "KOSDAQ"}
    assert len(ov["indices"][0]["spark"]) == 200 and "individual" in ov["indices"][0]["flows"]
    r = client.get("/api/market/rankings?kind=gainers&market=US").json()
    rates = [x["change_rate"] for x in r["items"]]
    assert r["duration"] == "1d" and rates == sorted(rates, reverse=True)
    assert client.get("/api/market/rankings?kind=nope").status_code == 400


def test_ai_meeting_endpoint(client):
    assert client.post("/api/ai/meeting", json={"symbol": "005930"}).status_code == 403
    r = client.post("/api/ai/meeting", json={"symbol": "005930"}, headers=client.h).json()
    import time
    for _ in range(50):
        run = client.get(f"/api/ai/run/{r['id']}").json()
        if run["status"] != "running":
            break
        time.sleep(0.1)
    assert run["status"] == "done" and run["final"]["rating"] == "Buy"
    runs = client.get("/api/ai/runs").json()
    assert runs[0]["rating_ko"] == "매수"
    roles = client.get("/api/ai/roles").json()
    assert len(roles["roles"]) == 14 and len(roles["stages"]) == 7
    ch = client.get("/api/ai/chart/005930").json()
    assert len(ch["candles"]) == 180 and ch["sma20"] and "support" in ch["levels"]
    tr = client.get("/api/ai/track").json()
    assert tr["summary"]["decisions"] == 1


def test_secrets_endpoint(client, fake_vault):
    r = client.put("/api/secrets", json={"kind": "toss", "key": "tsck_live_1234567890", "secret": "s" * 20},
                   headers=client.h).json()
    assert r["keys"]["toss"] and fake_vault["toss_client_id"] == "tsck_live_1234567890"
    r = client.put("/api/secrets", json={"kind": "anthropic", "key": "sk-ant-xxxxxxxxxxxx"}, headers=client.h).json()
    assert r["keys"]["anthropic"]
    assert client.delete("/api/secrets/toss", headers=client.h).json()["keys"]["toss"] is False
    assert client.put("/api/secrets", json={"kind": "x", "key": "k" * 20}, headers=client.h).status_code == 400
