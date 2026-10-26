import pytest

from qtrader.data import synthetic


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("QTRADER_HOME", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return tmp_path


@pytest.fixture(autouse=True)
def fake_vault(monkeypatch):
    """실제 윈도우 자격 증명 관리자를 건드리지 않는다."""
    store: dict[str, str] = {}
    monkeypatch.setattr("qtrader.vault.put", lambda n, v: store.__setitem__(n, v.strip()))
    monkeypatch.setattr("qtrader.vault.get", lambda n: store.get(n))
    monkeypatch.setattr("qtrader.vault.delete", lambda n: store.pop(n, None))
    return store


@pytest.fixture
def panel_kr():
    return {s: synthetic(s, days=500, start_price=50_000 * (1 + i * 0.2), seed=i)
            for i, s in enumerate(["005930", "000660", "035420", "005380"])}
