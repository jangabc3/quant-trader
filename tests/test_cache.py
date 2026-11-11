"""시장 화면 캐시: 받아 둔 값을 바로 돌려주고 뒤에서 새로 받기, 같은 값은 한 번만 받기."""
import threading
import time

from qtrader import market


def test_stale_value_returns_immediately_and_refreshes_in_background(monkeypatch):
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.3)
        return len(calls)
    k = "t:stale"
    market._cache.pop(k, None)
    assert market.cached(k, 0.05, slow) == 1  # 처음은 기다린다
    time.sleep(0.1)  # 신선한 시간(0.05초)이 지남
    t0 = time.monotonic()
    assert market.cached(k, 0.05, slow) == 1  # 예전 값을 바로 (0.3초 기다리지 않음)
    assert time.monotonic() - t0 < 0.1
    time.sleep(0.5)
    assert market.cached(k, 5, slow) == 2 and len(calls) == 2  # 뒤에서 새로 받아 둠


def test_same_key_fetched_once_when_requested_together():
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.3)
        return "v"
    k = "t:once"
    market._cache.pop(k, None)
    out = []
    ts = [threading.Thread(target=lambda: out.append(market.cached(k, 5, slow))) for _ in range(5)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert out == ["v"] * 5 and len(calls) == 1


def test_error_is_not_cached_and_does_not_block():
    k = "t:err"
    market._cache.pop(k, None)

    def boom():
        raise RuntimeError("토스 오류")
    try:
        market.cached(k, 5, boom)
    except RuntimeError:
        pass
    assert market.cached(k, 5, lambda: "ok") == "ok"


def test_live_snapshot_sends_index_rates_and_fx(monkeypatch):
    """실시간 스트림 한 번 분량: 종목 현재가·토스 등락률, 지수(IDX:KOSPI), 환율. 키가 없으면 아무것도 보내지 않는다."""
    monkeypatch.setattr(market, "_cache", {})

    class T:
        def quotes(self, syms):
            assert syms == ["005930", "SPY"]
            return {"005930": {"price": 241500.0, "change_rate": 0.0878}, "SPY": {"price": 600.5, "change_rate": 0.0008}}

        def indicator_prices(self, syms):
            assert syms == ["KOSPI", "KOSDAQ"]
            return [{"symbol": "KOSPI", "lastPrice": "6001.2"}, {"symbol": "KOSDAQ", "lastPrice": ""}]

        def exchange_rate(self):
            return {"rate": "1338.9", "basisPoint": "1.0"}
    d = market.live_snapshot(T(), ["005930", "SPY", "IDX:KOSPI", "IDX:KOSDAQ", "FX"])
    assert d["prices"] == {"005930": 241500.0, "SPY": 600.5} and d["rates"]["005930"] == 0.0878
    assert d["idx"] == {"KOSPI": 6001.2} and d["fx"] == {"rate": 1338.9, "change": 1.0}
    assert market.live_snapshot(None, ["005930"]) == {"prices": {}, "rates": {}, "idx": {}, "fx": None}
