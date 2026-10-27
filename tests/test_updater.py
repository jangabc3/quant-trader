"""자동 업데이트: 버전 비교, 지문(SHA-256) 확인, 압축 경로 검사."""
import hashlib
import io
import zipfile

import pytest

from qtrader import updater


def test_version_compare():
    assert updater.vtuple("v0.10.0") > updater.vtuple("0.9.9") > updater.vtuple("0.1")
    assert updater.vtuple("1.0.0-beta") == (1, 0, 0)


def test_check_without_repo_is_quiet():
    r = updater.check({"update": {"repo": ""}})
    assert r["available"] is False and r["current"]


class Resp:
    def __init__(self, data=b"", text=""):
        self.data, self.text = data, text

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, n):
        yield self.data


def _zip(names):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for n in names:
            z.writestr(n, "x")
    return b.getvalue()


@pytest.mark.parametrize("good_hash", [True, False])
def test_download_verifies_hash(tmp_path, monkeypatch, good_hash):
    import requests
    data = _zip(["QTrader/QTrader.exe", "QTrader/_internal/a.dll"])
    h = hashlib.sha256(data).hexdigest() if good_hash else "0" * 64
    monkeypatch.setattr(requests, "get", lambda url, **k: Resp(data) if url.endswith(".zip") else Resp(text=f"{h}  x.zip"))
    info = {"latest": "9.9.9", "zip": "https://x/QTrader-9.9.9-win.zip", "sha256": "https://x/QTrader-9.9.9-win.zip.sha256"}
    if good_hash:
        assert (updater.download(info, tmp_path) / "QTrader.exe").exists()
    else:
        with pytest.raises(ValueError, match="SHA-256"):
            updater.download(info, tmp_path)


def test_download_rejects_path_escape(tmp_path, monkeypatch):
    import requests
    data = _zip(["../evil.exe", "QTrader/QTrader.exe"])
    h = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(requests, "get", lambda url, **k: Resp(data) if url.endswith(".zip") else Resp(text=h))
    with pytest.raises(ValueError, match="경로"):
        updater.download({"latest": "1", "zip": "u.zip", "sha256": "u.sha256"}, tmp_path)


def test_publish_bumps_both_version_files(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("publish", Path(__file__).resolve().parents[1] / "packaging" / "publish.py")
    pub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pub)
    assert pub.bump("0.1.9", "patch") == "0.1.10" and pub.bump("0.1.9", "minor") == "0.2.0" and pub.bump("1.2.3", "major") == "2.0.0"
    init, pp = tmp_path / "__init__.py", tmp_path / "pyproject.toml"
    init.write_text('"""x"""\n\n__version__ = "0.1.0"\n', encoding="utf-8")
    pp.write_text('[project]\nname = "qtrader"\nversion = "0.1.0"\n\n[tool.x]\nversion = "9.9.9"\n', encoding="utf-8")
    monkeypatch.setattr(pub, "INIT", init)
    monkeypatch.setattr(pub, "PYPROJECT", pp)
    pub.set_version("0.2.0")
    assert pub.current() == "0.2.0" and 'version = "0.2.0"' in pp.read_text() and 'version = "9.9.9"' in pp.read_text()
