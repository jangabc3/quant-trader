"""토스증권 클라이언트(가짜 HTTP), AI 회의(가짜 LLM), AI 등급 -> 주문, 시장 화면 API."""
import json

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

    def run(self, system, user, web=False, schema=None, max_tokens=16000, model=None, context=None):
        self.calls.append({"system": system, "user": user, "web": web, "schema": bool(schema), "model": model,
                           "context": context})
        if schema is not None and "fact_checks" in schema["properties"]:
            return Reply(json.dumps(CHECK), data=CHECK, input_tokens=100, output_tokens=50, model=model or "")
        if schema:
            d = FINAL | {"rating": self.rating, "confidence": self.confidence}
            return Reply(json.dumps(d), data=d, input_tokens=100, output_tokens=50, model=model or "")
        return Reply("한줄요약: 테스트 보고서\n- 근거 1", sources=[{"title": "s", "url": "https://example.com"}],
                     input_tokens=100, output_tokens=50, cache_read=1000, model=model or "")


class Bars:
    name = "fake"

    def bars(self, market, symbol, days):
        return synthetic(symbol, days=300, seed=3)

    def price(self, market, symbol):
        return 100.0


# 체크리스트를 통과하도록 느슨한 규칙 (흐름 시험용)
LOOSE = {"require_trend": False, "require_momentum": False, "max_rsi": 101, "max_vol": 9, "min_fact_ok": 0}


def loose_cfg():
    cfg = config.load()
    cfg["ai"]["rules"] = LOOSE
    cfg["ai"]["require_approval"] = True  # 승인 흐름을 시험한다 (기본값은 자동 주문)
    return cfg


def test_meeting_fast_mode(tmp_path):
    st = Store(tmp_path / "m.db")
    llm = FakeLLM()
    run = meeting.start("005930", Bars(), st, config.load(), llm=llm, background=False)
    assert run["status"] == "done", run["error"]
    assert run["mode"] == "fast" and run["final"]["rating"] == "Buy"
    assert all(run["agents"][r.id]["status"] == "done" for r in ROLES)
    # 분석 3 + 검증관 + 강세·약세 2 + 대표 = 7번
    assert len(llm.calls) == 7 and sum(c["schema"] for c in llm.calls) == 2
    assert [c["web"] for c in llm.calls].count(True) == 3
    models = [c["model"] for c in llm.calls]
    assert models.count("claude-opus-5-5") == 2 and models.count("claude-sonnet-5-5") == 5
    # 모든 참석자가 같은 system 을 받아야 캐시가 맞는다 (역할은 user 에)
    assert len({c["system"] for c in llm.calls}) == 1
    assert "투자자 프로필" in llm.calls[0]["system"] and "Jegadeesh" in llm.calls[0]["system"]
    assert "[너의 역할]" in llm.calls[0]["user"] and llm.calls[0]["context"].startswith("오늘은")
    assert run["calls"] == 7 and run["cost_usd"] > 0 and run["cache_read"] > 0
    assert run["plan"]["stop"] < run["plan"]["entry"] < run["plan"]["take"] and run["checklist"]["rules"]
    assert run["agents"]["earn"]["status"] == "done" and "대표" not in run["agents"]["earn"]["bubble"]
    saved = st.latest_decision("005930", run["trade_date"])
    assert saved["plan"]["take"] == run["plan"]["take"] and saved["checklist"] is not None


def test_meeting_precise_mode(tmp_path):
    st = Store(tmp_path / "m.db")
    cfg = config.load()
    cfg["ai"]["mode"] = "precise"
    llm = FakeLLM()
    run = meeting.start("005930", Bars(), st, cfg, llm=llm, background=False)
    assert run["status"] == "done", run["error"]
    # 분석가 5 + 검증관 + 강세/약세 2 + 팀장 + 트레이더 + 리스크 3 + 대표 = 14번
    assert len(llm.calls) == 14 and [c["web"] for c in llm.calls].count(True) == 5
    assert [c["model"] for c in llm.calls].count("claude-opus-5-5") == 3
    assert run["check"]["scores"]["flow"] == 72 and run["facts"]["levels"]["sma20"]
    assert all(v == "done" for v in run["stages"].values()) and len(run["handoffs"]) >= 10
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
    cfg = loose_cfg()
    for sym, r, c in (("005930", "Buy", 0.9), ("000660", "Sell", 0.9), ("035420", "Hold", 0.9), ("005380", "Buy", 0.3)):
        meeting.start(sym, Bars(), st, cfg, llm=FakeLLM(r, c), background=False)
    today = st.ai_runs(1)[0]["trade_date"]
    panel_ = {s: synthetic(s, days=300, seed=3) for s in ["005930", "000660", "035420", "005380", "373220"]}
    target, keep, notes = ai_targets(st, cfg, panel_, today)
    assert set(target) == {"005930", "000660"} and target["000660"] == 0.0 and 0 < target["005930"] <= 0.2
    assert keep == {"035420", "005380", "373220"}
    by = {n["symbol"]: n for n in notes}
    assert by["373220"]["action"] == "회의 결과 없음" and by["005930"]["checklist"]["passed"]


def test_checklist_blocks_impulse_buy(tmp_path):
    """규칙을 엄격히 하면 같은 '매수' 의견도 막힌다 (하락 추세 종목)."""
    import pandas as pd
    from qtrader import principles
    idx = pd.bdate_range("2025-01-01", periods=300)
    down = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": [200 - i * 0.4 for i in range(300)],
                         "volume": 1.0}, index=idx)
    down["high"], down["low"] = down["close"] * 1.01, down["close"] * 0.99
    cl = principles.checklist(FINAL, CHECK, down, [{"warningType": "OVERHEATED", "endDate": None}])
    failed = {r["key"] for r in cl["rules"] if not r["ok"]}
    assert not cl["passed"] and {"trend", "tsmom", "mom", "warnings"} <= failed
    ok = principles.checklist(FINAL, CHECK, down, [], LOOSE)
    assert ok["passed"] and 0 < ok["size"]["weight_factor"] <= 0.5
    weak = principles.checklist(FINAL | {"take_profit_pct": 5}, CHECK, down, [], LOOSE)
    assert not weak["passed"]  # 손익비 5/7 < 2


def test_trade_plan_prices():
    from qtrader import principles
    p = principles.trade_plan(FINAL | {"entry_low": 70000, "entry_high": 72000, "stop_loss_pct": 7, "take_profit_pct": 15},
                              71500, 10_000_000, 0.2, 0.25, "KR")
    assert p["entry"] == 71000 and p["stop"] == 66030 and p["take"] == 81650
    assert p["qty"] == 28 and p["max_loss"] == 28 * (71000 - 66030)
    bad = principles.trade_plan(FINAL, 71500, 10_000_000, 0.2, 0.25, "KR")  # 95~100원: 단위 오류 -> 현재가 기준
    assert bad["entry"] == 71500


def test_engine_ai_strategy_keeps_undecided(isolated_home):
    cfg = loose_cfg()
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
    pend = ctx.store.approvals("pending")
    assert len(pend) == 1 and "AI 회의 Buy" in pend[0]["reason"] and "손절" in pend[0]["reason"]
    assert run_market(ctx, Market.KR, force=True)["orders"] == []  # 같은 날 승인 대기는 하나만
    from qtrader.engine import check_exits, execute_approval
    res = execute_approval(ctx, pend[0]["id"])
    assert res["status"] == "filled" and ctx.broker.account(Market.KR).positions["005930"].qty > 0
    plans = ctx.store.active_plans("paper", "KR")
    assert len(plans) == 1 and plans[0]["stop"] < plans[0]["take"]
    with pytest.raises(ValueError):
        execute_approval(ctx, pend[0]["id"])
    # 손절가를 현재가 위로 옮기면 자동 손절
    ctx.store.exec("update plans set stop=? where symbol='005930'", (10 ** 9,))
    ex = check_exits(ctx, Market.KR)
    assert ex and ex[0]["reason"] == "stop" and "005930" not in ctx.broker.account(Market.KR).positions
    assert ctx.store.recent_stop("paper", "KR", "005930", 5)


def test_approval_blocked_by_kill_switch(isolated_home):
    cfg = loose_cfg()
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


def test_auto_order_reports_why(isolated_home):
    """승인 없이 바로 주문하고, '왜 사고팔았나요'에 회의 근거가 붙는다."""
    from qtrader.web.app import create_app
    cfg = loose_cfg()
    cfg["ai"]["require_approval"] = False
    cfg["markets"]["KR"]["strategy"] = "ai"
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    ctx = build_context()
    meeting.start("005930", ctx.data, ctx.store, ctx.cfg, llm=FakeLLM("Buy", 0.9), background=False)
    r = run_market(ctx, Market.KR)
    assert [(o["symbol"], o["side"], o["status"]) for o in r["orders"]] == [("005930", "buy", "filled")]
    why = TestClient(create_app()).get("/api/trades/why").json()
    assert why[0]["symbol"] == "005930" and why[0]["run"]["rating"] == "Buy" and why[0]["run"]["plan"]["stop"] > 0


def test_compare_accounts_follow_same_meeting(isolated_home):
    from qtrader import compare
    cfg = loose_cfg()
    cfg["ai"]["require_approval"] = False
    cfg["markets"]["KR"]["strategy"] = "ai"
    cfg["compare"] = {"enabled": True, "modes": ["long", "short"]}
    config.save(cfg)
    write_demo_csv(isolated_home / "data", cfg)
    ctx = build_context()
    meeting.start("005930", ctx.data, ctx.store, ctx.cfg, llm=FakeLLM("Buy", 0.9), background=False)
    run_market(ctx, Market.KR)
    out = compare.run(ctx, Market.KR)
    assert [o["mode"] for o in out] == ["long", "short"] and all(o["orders"] == 1 for o in out)
    short = ctx.store.active_plans("cmp-short", "KR")[0]
    assert short["stop"] == pytest.approx(short["entry"] * 0.975) and short["horizon"] == 3  # 단기 규칙으로 다시 잡힘
    rows = compare.summary(ctx, Market.KR)
    assert {r["mode"] for r in rows} == {"long", "short"} and all(r["positions"] == 1 for r in rows)


def test_cost_counts_web_search_fee():
    """웹 검색은 토큰과 별도로 1번에 0.01달러. 빠지면 월 비용 상한이 실제보다 적게 센다."""
    from types import SimpleNamespace as NS
    from qtrader.agents.llm import LLM, cost_usd, usage_cost

    class Client:
        def __init__(self):
            self.beta = NS(messages=NS(create=lambda **kw: NS(
                content=[NS(type="text", text="보고서")], stop_reason="end_turn",
                usage=NS(input_tokens=1000, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                         server_tool_use=NS(web_search_requests=4)))))
    r = LLM("claude-sonnet-5-5", client=Client()).run("s", "u", web=True)
    tokens = cost_usd("claude-sonnet-5-5", 1000, 100)
    assert r.web_searches == 4 and r.cost_usd() == pytest.approx(tokens + 0.04)
    assert usage_cost("claude-sonnet-5-5", NS(input_tokens=1000, output_tokens=100, server_tool_use=NS(web_search_requests=2))) \
        == pytest.approx(tokens + 0.02)
    assert usage_cost("claude-sonnet-5-5", None) == 0


def test_claude_client_does_not_wait_forever(fake_vault):
    """응답 하나를 기본 10분 x 재시도 3번 기다리면 회의가 수십 분 멈춘 것처럼 보이고 다음 회의도 못 연다."""
    from qtrader.agents.llm import make_client
    fake_vault["anthropic_api_key"] = "sk-ant-test"
    c = make_client()
    assert c.max_retries == 2 and float(c.timeout) == 300.0


def test_decision_guide_no_longer_defaults_to_hold():
    """'근거가 약하면 Hold' 만 있으면 숫자 거르기를 통과한 후보도 다 보류로 끝났다. 위험 신호가 있을 때만 보류."""
    from qtrader.agents import roles
    g = roles.decision_guide(False)
    assert "Overweight" in g and "위험 신호" in g and "가상매매" not in g
    assert "가상매매 연습" in roles.decision_guide(True)
    assert "보류(Hold: 새로 사지 않고 지켜봄)'가 정답" not in roles.RULES


def test_track_compares_with_price_at_meeting_time():
    """장중 회의는 그날 종가가 아니라 회의 때 가격과 비교한다 (예전엔 같은 날이면 늘 0.00%)."""
    import pandas as pd
    from qtrader.agents import track
    df = pd.DataFrame({"close": [100.0, 110.0], "high": [100.0, 112.0], "low": [99.0, 108.0]},
                      index=pd.to_datetime(["2026-10-27", "2026-10-28"]))
    runs = [{"id": "a", "symbol": "005930", "trade_date": "2026-10-28", "status": "done", "plan": {"price": 105},
             "final": {"rating": "Overweight", "confidence": 0.6, "headline": "작게 시작"}},
            {"id": "b", "symbol": "005930", "trade_date": "2026-10-28", "status": "done",
             "final": {"rating": "Hold", "confidence": 0.5, "headline": "실적 발표 앞둠"}}]
    rows = track.evaluate(runs, lambda s: df)["rows"]
    assert rows[0]["price"] == 105 and rows[0]["return"] == round(110 / 105 - 1, 4) and rows[0]["days"] == 0
    assert rows[1]["return"] == 0 and rows[1]["correct"] is None and rows[1]["headline"] == "실적 발표 앞둠"


def test_fact_check_unverifiable_counts_half_wrong_still_fails():
    """'확인 불가'는 틀린 게 아니다: 확인 5·확인 불가 4 는 통과 (예전엔 56% 로 걸림). 틀림이 하나라도 있으면 실패."""
    from qtrader import principles
    import pandas as pd
    import numpy as np
    bars = pd.DataFrame({"close": np.linspace(100, 150, 300)}, index=pd.bdate_range(end="2026-10-28", periods=300))
    bars["high"], bars["low"], bars["volume"] = bars["close"], bars["close"], 1e6
    fin = {"rating": "Overweight", "confidence": 0.7, "stop_loss_pct": 5, "take_profit_pct": 12}

    def facts(ok, unsure, wrong):
        fc = [{"status": "확인됨"}] * ok + [{"status": "확인 불가"}] * unsure + [{"status": "틀림"}] * wrong
        cl = principles.checklist(fin, {"fact_checks": fc}, bars, [], principles.DEFAULT_RULES)
        return next(r for r in cl["rules"] if r["key"] == "facts")
    assert facts(5, 4, 0)["ok"] and "확인 불가 4" in facts(5, 4, 0)["value"]
    assert not facts(8, 0, 1)["ok"] and not facts(1, 8, 0)["ok"]
