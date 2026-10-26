import pytest

from qtrader.data import synthetic


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QTRADER_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def panel_kr():
    return {s: synthetic(s, days=500, start_price=50_000 * (1 + i * 0.2), seed=i)
            for i, s in enumerate(["005930", "000660", "035420", "005380"])}
