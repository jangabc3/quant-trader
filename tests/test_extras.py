"""실현 손익·세금, 업종 쏠림, 가격 알림, AI 비용 상한, 하루 요약."""
import json
from datetime import datetime

import pytest

from qtrader import config, extras
from qtrader.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "s.db")


def order(store, side, qty, price, sym="AAPL", market="US", d="2026-03-02"):
    store.add_order(trade_date=d, account="toss", market=market, symbol=sym, side=side, qty=qty, price=price,
                    status="filled", broker_order_id="", strategy="ai", message="근거 | ok")


def test_realized_average_cost(store):
    order(store, "buy", 10, 100)
    order(store, "buy", 10, 200)  # 평균 150
    order(store, "sell", 5, 180)  # +150
    order(store, "sell", 15, 140)  # -150
    r = extras.realized(store, "toss", "US")
    assert r["total"] == pytest.approx(0) and r["trades"] == 2 and r["win_rate"] == 0.5


def test_us_tax_after_deduction(store):
    order(store, "buy", 10, 100, d="2026-01-05")
    order(store, "sell", 10, 400, d="2026-02-05")  # +$3,000
    t = extras.tax_summary(store, "toss", "2026", fx=1000)
    assert t["us_krw"] == 3_000_000 and t["us_taxable_krw"] == 500_000
    assert t["us_tax_krw"] == pytest.approx(110_000)


def test_concentration_warns_sector_and_weight():
    c = extras.concentration([{"symbol": "005930", "value": 50}, {"symbol": "000660", "value": 30},
                              {"symbol": "035720", "value": 20}], max_weight=0.3)
    assert any("반도체" in w for w in c["warnings"]) and any("005930" in w for w in c["warnings"])
    assert c["sectors"][0]["name"] == "반도체"


def test_alerts_hit_and_removed(store):
    cfg = {"alerts": [{"symbol": "005930", "op": "above", "price": 70000}, {"symbol": "NVDA", "op": "below", "price": 100}]}
    saved = []
    hit = extras.check_alerts(store, cfg, lambda s: 71000 if s == "005930" else 120, saved.append)
    assert [h["symbol"] for h in hit] == ["005930"] and len(cfg["alerts"]) == 1 and saved
    assert store.rows("select * from events where level='notice'")


def test_monthly_cost_cap_blocks_meeting(store):
    store.exec("insert into ai_runs (id, symbol, market, trade_date, started_at, status, reports) values (?,?,?,?,?,?,?)",
               ("r1", "005930", "KR", "2026-01-01", datetime.now().isoformat(), "done", json.dumps({"cost_usd": 30})))
    assert extras.month_cost_krw(store) == pytest.approx(30 * extras.KRW_PER_USD)
    from qtrader.agents import meeting
    cfg = config.load()
    cfg["ai"]["monthly_cost_cap_krw"] = 10000
    with pytest.raises(ValueError, match="상한"):
        meeting.start("005930", None, store, cfg, background=False)


def test_daily_report_text(store):
    store.add_equity("2026-03-01", "toss", "KR", 1_000_000, 1_000_000)
    store.add_equity("2026-03-02", "toss", "KR", 1_010_000, 500_000)
    order(store, "buy", 3, 70000, sym="005930", market="KR")
    r = extras.daily_report(store, "toss", "KR", "2026-03-02")
    assert "+1.00%" in r["text"] and "매수 005930 3주" in r["text"]


def test_feeds_keep_category_after_save(tmp_path, monkeypatch):
    monkeypatch.setenv("QTRADER_HOME", str(tmp_path))
    cfg = config.load()
    cfg["news"]["feeds"] = [{"name": "A", "url": "https://a.example/rss", "cat": "global"}]
    config.save(cfg)
    assert config.load()["news"]["feeds"][0]["cat"] == "global"


def test_concentration_counts_cash():
    c = extras.concentration([{"symbol": "005930", "value": 250}], max_weight=0.2, cash=9750)
    assert c["warnings"] == [] and c["sectors"][0]["weight"] == pytest.approx(0.025)


def test_screener_picks_held_first_and_filters(isolated_home):
    from qtrader.data import write_demo_csv
    from qtrader.engine import build_context
    from qtrader.models import Market
    from qtrader import screener
    write_demo_csv(isolated_home / "data", config.load())
    ctx = build_context()
    picks = screener.candidates(ctx, Market.KR, 3)
    # 후보는 필수 기준(200일선·변동성·거래대금)을 모두, 추가 기준(모멘텀·과열)은 허용 개수까지만 못 넘는다
    assert len(picks) <= 3 and all(screener.passes(p["fails"], 2) for p in picks if not p["held"])
    first = screener.todays(ctx, Market.KR, "2026-10-27", 3)
    assert screener.todays(ctx, Market.KR, "2026-10-27", 3) == first  # 하루 한 번만 고른다


def test_performance_round_trips(store):
    order(store, "buy", 10, 100, d="2026-03-02")
    order(store, "sell", 10, 110, d="2026-03-05")  # +100
    order(store, "buy", 5, 200, d="2026-03-06")
    store.add_order(trade_date="2026-03-09", account="toss", market="US", symbol="AAPL", side="sell", qty=5, price=190,
                    status="filled", broker_order_id="", strategy="ai-exit", message="손절: x | ok")  # -50
    r = extras.performance(store, "toss", "US")
    s = r["summary"]
    assert s["count"] == 2 and s["realized"] == 50 and s["win_rate"] == 0.5 and s["profit_factor"] == 2
    assert r["trades"][0]["why"] == "손절" and r["trades"][1]["days"] == 3 and s["mdd"] == -50


def test_telegram_find_chat_from_own_bot(monkeypatch):
    """다른 봇 없이, 내 봇에게 보낸 메시지에서 대화방 번호를 찾는다."""
    from qtrader import checks, vault

    class R:
        def __init__(self, d):
            self.d = d

        def json(self):
            return self.d

    class S:
        def __init__(self, updates):
            self.updates = updates

        def get(self, url, **k):
            if url.endswith("/getMe"):
                return R({"ok": True, "result": {"username": "my_qtrader_bot"}})
            return R({"ok": True, "result": self.updates})

    monkeypatch.setattr(vault, "get", lambda k: "123:ABC" if k == "telegram_bot_token" else None)
    upd = [{"message": {"chat": {"id": 555, "type": "private", "first_name": "길동"}, "text": "Gd"}},
           {"message": {"chat": {"id": -100, "type": "group"}}}]
    r = checks.telegram_find_chat(S(upd))
    assert r["chat_id"] == "555" and r["name"] == "길동" and r["bot"] == "@my_qtrader_bot"
    with pytest.raises(RuntimeError, match="@my_qtrader_bot"):
        checks.telegram_find_chat(S([]))
