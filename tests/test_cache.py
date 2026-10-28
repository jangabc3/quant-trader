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
