"""가상 계좌 처음부터 다시, '왜 후보가 적은지' 규칙별 제외 숫자."""
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from qtrader import config, conditional, screener
from qtrader.data import write_demo_csv
from qtrader.models import Market
from qtrader.storage import Store
from qtrader.web import app as web_app
from qtrader.web.app import create_app


@pytest.fixture
def client(isolated_home):
    write_demo_csv(isolated_home / "data", config.load())
    app = create_app()
    c = TestClient(app)
    c.h = {"x-qtrader-token": app.state.token}
    return c


def _ctx():
    from qtrader.engine import build_context
    return build_context()


def test_reset_needs_token(client):
    assert client.post("/api/paper/reset", json={"market": "KR"}).status_code == 403


def test_reset_refused_outside_paper(client, monkeypatch):
    real = web_app.build_context

    def live_ctx():  # 실전 계좌처럼 보이게 (키 없이)
        c = real()
        c.cfg["mode"] = "toss_real"
        c.broker = SimpleNamespace(name="toss", live=True)
        return c

    monkeypatch.setattr(web_app, "build_context", live_ctx)
    r = client.post("/api/paper/reset", json={"market": "ALL"}, headers=client.h)
    assert r.status_code == 400 and "가상매매" in r.json()["detail"]


def test_reset_restores_cash_and_closes_plans(client):
    c = _ctx()
    cash0 = config.load()["paper"]["initial_cash"]
    s = c.store
    # 체험 가격으로 산 삼성전자 (+166% 처럼 보이던 상태), 미국 계좌에도 하나
    s.exec("insert into positions values ('paper','KR','005930',10,84000)")
    s.exec("update cash set amount=? where account='paper' and market='KR'", (cash0["KR"] - 840000,))
    s.exec("insert into positions values ('paper','US','AAPL',3,150)")
    s.add_order(trade_date="2026-10-01", account="paper", market="KR", symbol="005930", side="buy", qty=10, price=84000,
                status="filled", broker_order_id="paper", strategy="ai", message="체험")
    s.add_equity("2026-10-01", "paper", "KR", cash0["KR"], cash0["KR"] - 840000)
    s.save_plan("paper", "KR", "005930", "r1", 84000, 80000, 90000)
    s.save_plan("paper", "US", "AAPL", "r2", 150, 140, 170)
    s.save_ai_run({"id": "run1", "symbol": "005930", "market": "KR", "trade_date": "2026-10-01",
                   "started_at": "2026-10-01T00:00:00", "status": "done"})
    conditional.create(c, "005930", "buy", 1, 50000)
    before_events = len(s.events(1000))

    r = client.post("/api/paper/reset", json={"market": "KR"}, headers=client.h)
    assert r.status_code == 200, r.text
    assert r.json()["markets"]["KR"] == {"positions": 1, "orders": 1, "plans": 1, "cash": float(cash0["KR"])}

    acct = _ctx().broker.account(Market.KR)
    assert acct.cash == cash0["KR"] and not acct.positions
    assert not s.active_plans("paper", "KR") and s.equity_history("paper", "KR") == []
    assert s.rows("select close_reason from plans where symbol='005930'")[0]["close_reason"] == "reset"
    assert not s.rows("select 1 from orders where account='paper' and market='KR'")
    assert [x["status"] for x in conditional._load(s)] == ["canceled"]
    # 미국 계좌는 그대로, AI 회의 기록·이벤트 기록은 남는다
    assert "AAPL" in _ctx().broker.account(Market.US).positions and s.active_plans("paper", "US")
    assert s.ai_runs(10)[0]["id"] == "run1"
    assert len(s.events(1000)) > before_events and "처음부터 다시" in s.events(1)[0]["message"]

    r = client.post("/api/paper/reset", json={"market": "ALL"}, headers=client.h)
    assert r.status_code == 200 and set(r.json()["markets"]) == {"KR", "US"}
    us = _ctx().broker.account(Market.US)
    assert us.cash == cash0["US"] and not us.positions


def test_reset_rejects_unknown_market(client):
    assert client.post("/api/paper/reset", json={"market": "JP"}, headers=client.h).status_code == 400


# ---- 왜 후보가 적은지 ----

OK = {"last": 110, "sma200": 100, "ret12": 0.3, "mom12_1": 0.25, "rsi": 55, "vol": 0.25,
      "near_high": 0.95, "r5": 0.01, "vol_ratio": 1.1}
F = {
    "111110": OK,                                                    # 통과
    "111120": OK | {"mom12_1": 0.2},                                 # 통과했지만 나쁜 공시
    "111130": OK | {"mom12_1": 0.1},                                 # 통과, 재무 감점
    "111140": OK,                                                    # 통과했지만 우선주 (기본 정보)
    "111150": OK | {"last": 90, "ret12": -0.2, "mom12_1": -0.1},     # 200일선 아래 + 모멘텀 음수
    "111160": OK | {"rsi": 85},                                      # 과열
    "111170": OK | {"vol": 0.9},                                     # 변동성
    "111180": OK | {"last": 90},                                     # 200일선 아래 (모멘텀은 양수)
}


class FakeData:
    name = "toss"

    class client:  # 토스 종목 기본 정보만 흉내 (순위 API 는 없어서 조용히 건너뛴다)
        @staticmethod
        def stocks(syms):
            return [{"symbol": s, "status": "ACTIVE", "isCommonShare": s != "111140"} for s in syms]

    def bars(self, market, s, n):
        if s not in F:
            raise KeyError(s)  # 자료 없음
        df = pd.DataFrame({"close": [1.0] * 300})
        df.attrs["s"] = s
        return df


def test_candidate_exclusion_counts(tmp_path, monkeypatch):
    cfg = config.load()
    cfg["watchlist"] = []
    cfg["markets"]["KR"]["universe"] = list(F) + ["999990"]
    monkeypatch.setattr(screener, "GROWTH", {"KR": [], "US": []})
    monkeypatch.setattr(screener, "factors", lambda bars: F[bars.attrs["s"]])
    monkeypatch.setattr(screener, "disclosures", lambda s, home: {"bad": "유상증자 결정"} if s == "111120" else {})
    monkeypatch.setattr(screener, "quality", lambda ctx, s: {"weak": True} if s == "111130" else {})
    broker = SimpleNamespace(name="paper", account=lambda m: SimpleNamespace(positions={}))
    ctx = SimpleNamespace(cfg=cfg, store=Store(tmp_path / "s.db"), data=FakeData(), broker=broker)

    st = {}
    picks = screener.candidates(ctx, Market.KR, 10, st)
    assert [p["symbol"] for p in picks] == ["111110", "111130"]
    assert st == {"pool": 9, "no_data": 1, "checked": 8, "trend": 2, "momentum": 1, "rsi": 1, "vol": 1, "illiquid": 0,
                  "rules_out": 4, "basic": 1, "disclosure": 1, "weak_finance": 1, "passed": 3, "picked": 2, "full_scan": 0}
    # 예전처럼 stats 없이 불러도 목록은 같다
    assert screener.candidates(ctx, Market.KR, 10) == picks
    # 하루 한 번 저장한 뒤에도 같은 숫자를 돌려준다
    s1, s2 = {}, {}
    screener.todays(ctx, Market.KR, "2026-10-27", 10, s1)
    screener.todays(ctx, Market.KR, "2026-10-27", 10, s2)
    assert s1 == s2 == st


def test_picks_api_stats_with_demo_data(client):
    old = client.get("/api/picks?market=KR").json()
    assert isinstance(old, list)  # 기본 응답은 예전 그대로
    r = client.get("/api/picks?market=KR&stats=true&refresh=true").json()
    st = r["excluded"]
    assert [p["symbol"] for p in r["items"]] == [p["symbol"] for p in old]
    assert st["pool"] == st["no_data"] + st["checked"] > 0
    assert st["passed"] <= st["checked"] - st["rules_out"] and st["picked"] == sum(not p["held"] for p in r["items"])


def test_full_market_scan_feeds_candidates(tmp_path, monkeypatch):
    """국내 전체를 훑어 둔 결과가 있으면 순위 목록 밖 종목도 후보가 되고, 일봉을 다시 받지 않는다. 거래가 너무 적은 종목은 뺀다."""
    from qtrader import scan
    from qtrader.engine import trade_date
    cfg = config.load()
    cfg["watchlist"], cfg["markets"]["KR"]["universe"] = [], []
    monkeypatch.setattr(screener, "GROWTH", {"KR": [], "US": []})
    monkeypatch.setattr(screener, "ORIGIN", {})  # 앞 시험이 남긴 '어디서 왔는지' 기록이 섞이지 않게
    monkeypatch.setattr(screener, "disclosures", lambda s, home: {})
    monkeypatch.setattr(screener, "quality", lambda ctx, s: {})
    monkeypatch.setattr(screener, "factors", lambda bars: F[bars.attrs["s"]] | {"amt20": 5e8 if bars.attrs["s"] != "111180" else 1e7})
    broker = SimpleNamespace(name="paper", account=lambda m: SimpleNamespace(positions={}))
    data = FakeData()
    ctx = SimpleNamespace(cfg=cfg, store=Store(tmp_path / "s.db"), data=data, broker=broker)
    master = [{"symbol": s, "name": f"회사{s}", "market": "KOSPI", "type": "STOCK"} for s in F] + \
             [{"symbol": "111190", "name": "어느스팩1호", "market": "KOSDAQ", "type": "STOCK"},
              {"symbol": "111200", "name": "어느회사우", "market": "KOSPI", "type": "STOCK"},
              {"symbol": "069500", "name": "KODEX 200", "market": "KOSPI", "type": "ETF"}]
    syms = scan.universe(master)
    assert syms == list(F)  # 스팩·우선주·ETF 는 뺀다
    today = trade_date(Market.KR)
    scan.run(ctx, Market.KR, today, syms + ["999990"])
    assert scan.usable(ctx.store, Market.KR, today)["total"] == 9
    data.bars = lambda *a: (_ for _ in ()).throw(AssertionError("다시 받으면 안 됨"))
    st = {}
    picks = screener.candidates(ctx, Market.KR, 10, st)
    assert {p["symbol"] for p in picks} == {"111110", "111120", "111130"} and st["full_scan"] == 9 and st["no_data"] == 1
    assert "국내 전체 훑기" in picks[0]["reason"]
    assert scan.maybe_start(ctx, Market.KR, today) is False  # 오늘 이미 훑음
