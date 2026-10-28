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


def _git(cwd, *a):
    import subprocess
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                        "PATH": __import__("os").environ["PATH"], "HOME": str(cwd)})


def test_git_mode_finds_new_commits_and_prepares_reinstall(tmp_path):
    """A 방식: 설치한 커밋과 GitHub main 을 비교해 알리고, '지금 업데이트'는 'QTrader 설치.cmd --update' 를 준비한다."""
    import json
    import subprocess
    origin, src, home = tmp_path / "origin", tmp_path / "src", tmp_path / "home"
    home.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    (origin / "a.txt").write_text("1")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "처음")
    _git(tmp_path, "clone", "-q", str(origin), str(src))
    (src / updater.INSTALLER).write_text("@echo off")
    assert updater.source_info(home) is None  # 설치 기록이 없으면 B 방식
    updater.mark_installed(home, src)
    info = updater.source_info(home)
    run = subprocess.run
    r = updater.check_git(info, force=True, run=run)
    assert r["mode"] == "git" and r["available"] is False and r["current_rev"] == info["rev"][:7]
    (origin / "a.txt").write_text("2")
    _git(origin, "commit", "-q", "-am", "fix(stock): 호가 겹침 고치기")
    r = updater.check_git(info, force=True, run=run)
    assert r["available"] and r["behind"] == 1 and "호가 겹침" in r["notes"]
    updater.write_git_pending(info, home)
    p = json.loads((home / "updates" / "pending.json").read_text(encoding="utf-8"))
    assert p["args"][-2:] == [str(src / updater.INSTALLER), "--update"] and p["cwd"] == str(src)


def test_git_mode_reports_fetch_failure(tmp_path):
    class Fail:
        returncode, stdout, stderr = 128, "", "fatal: could not read Username for 'https://github.com'"
    r = updater.check_git({"src": str(tmp_path), "rev": "abc"}, force=True, run=lambda *a, **k: Fail())
    assert r["available"] is False and "확인 실패" in r["reason"] and "Username" in r["reason"]


def test_finish_pending_runs_args(tmp_path, monkeypatch):
    import json
    import subprocess
    (tmp_path / "updates").mkdir()
    (tmp_path / "updates" / "pending.json").write_text(json.dumps({"args": ["cmd", "/c", "x.cmd", "--update"], "cwd": "D:/src"}))
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: seen.update(args=args, cwd=kw.get("cwd")))
    assert updater.finish_pending(tmp_path) and seen == {"args": ["cmd", "/c", "x.cmd", "--update"], "cwd": "D:/src"}
    assert not (tmp_path / "updates" / "pending.json").exists()


def test_install_record_also_next_to_program(tmp_path):
    """설치 기록은 데이터 폴더와 프로그램 폴더 두 곳에. 데이터 폴더 기록이 없어도 업데이트 버튼이 나온다."""
    import subprocess
    origin, src, home, app = tmp_path / "o", tmp_path / "src", tmp_path / "home", tmp_path / "app"
    home.mkdir()
    app.mkdir()
    _git(tmp_path, "init", "-q", "-b", "main", str(origin))
    (origin / "a.txt").write_text("1")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "처음")
    _git(tmp_path, "clone", "-q", str(origin), str(src))
    updater.mark_installed(home, src, run=subprocess.run, app_dir=app)
    (home / "source.json").unlink()  # 데이터 폴더 기록이 없어져도
    info = updater.source_info(home, app_dir=app)
    assert info and info["src"] == str(src.resolve()) and len(info["rev"]) == 40
