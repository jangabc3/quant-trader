"""자동 업데이트. 두 가지 방식이 있다.

A. 내 PC 에서 다시 만들기 (지금 쓰는 방식, 혼자 쓸 때)
  'QTrader 설치.cmd' 가 설치할 때 소스 폴더와 만든 커밋을 %APPDATA%\\QTrader\\source.json 에 적는다.
  프로그램은 6시간마다 그 폴더에서 git fetch 로 GitHub main 과 비교하고, 새 커밋이 있으면 알린다.
  '지금 업데이트'를 누르면 프로그램이 꺼지고 'QTrader 설치.cmd --update' 가 최신 코드 받기 -> 다시 만들기
  -> 설치 -> 다시 켜기를 한다. 공개 저장소나 버전 번호 올리기가 필요 없다 (main 에 합치면 새 버전).

B. GitHub Releases (다른 사람에게 나눠 줄 때). source.json 이 없고 update.repo 가 있으면 이 방식.
배포하는 쪽 (개발자): packaging\\publish.cmd "바뀐 점" 한 번이면 아래 1~3 을 모두 한다. 손으로 할 때는
  1. qtrader/__init__.py 의 __version__ 을 올린다 (예: 0.2.0)
  2. packaging\\release.cmd 실행 -> dist\\QTrader-0.2.0-win.zip 과 .sha256 이 생긴다
  3. GitHub 저장소 Releases 에 태그 v0.2.0 으로 두 파일을 올리고, 본문에 바뀐 점을 적는다

받는 쪽 (사용자): 하루 한 번 새 버전을 확인해 화면 위에 알려 준다. '지금 업데이트'를 누르면
  내려받기 -> SHA-256 확인 -> 프로그램 종료 -> 새 파일로 바꾸기 -> 다시 켜기. 데이터(키·기록·설정)는
  %APPDATA%\\QTrader 에 있어서 그대로 남는다. 설치본(QTrader.exe)에서만 동작하고, 개발 버전은 git pull 로 받는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import __version__

RESTART_CODE = 75  # serve 가 이 코드로 끝나면 supervise 가 업데이트를 마무리한다
API = "https://api.github.com/repos/{repo}/releases/latest"


def vtuple(v: str) -> tuple:
    out = []
    for x in v.lstrip("vV").split("."):
        n = "".join(c for c in x if c.isdigit())
        out.append(int(n or 0))
    return tuple(out + [0] * (3 - len(out)))


def frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def check(cfg: dict, store=None, force: bool = False) -> dict:
    """새 버전이 있는지. 6시간에 한 번만 GitHub 에 묻는다."""
    repo = ((cfg.get("update") or {}).get("repo") or "").strip()
    base = {"current": __version__, "available": False, "repo": repo, "installed": frozen()}
    if not repo:
        return base | {"reason": "업데이트 저장소가 정해지지 않았어요"}
    if os.environ.get("QTRADER_OFFLINE"):
        return base | {"reason": "오프라인"}
    if store is not None and not force:
        c = json.loads(store.get("update_check") or "{}")
        if c.get("repo") == repo and time.time() - c.get("at", 0) < 6 * 3600:
            return base | c.get("res", {}) | {"current": __version__, "available": vtuple(c.get("res", {}).get("latest", "0")) > vtuple(__version__)}
    import requests
    try:
        r = requests.get(API.format(repo=repo), timeout=10, headers={"Accept": "application/vnd.github+json"})
        r.raise_for_status()
        d = r.json()
    except Exception as e:
        return base | {"reason": f"확인 실패: {e}"}
    assets = {a["name"]: a["browser_download_url"] for a in d.get("assets", [])}
    zipn = next((n for n in assets if n.endswith("-win.zip")), None)
    res = {"latest": d.get("tag_name", "").lstrip("vV"), "notes": (d.get("body") or "")[:4000], "published": d.get("published_at"),
           "zip": assets.get(zipn), "sha256": assets.get(f"{zipn}.sha256") if zipn else None, "page": d.get("html_url")}
    if store is not None:
        store.set("update_check", json.dumps({"repo": repo, "at": time.time(), "res": res}))
    return base | res | {"available": bool(res["latest"]) and vtuple(res["latest"]) > vtuple(__version__)}


def download(info: dict, home: Path) -> Path:
    """내려받고 SHA-256 을 확인해 풀어 둔다. 풀린 폴더를 돌려준다."""
    import requests
    if not info.get("zip") or not info.get("sha256"):
        raise ValueError("릴리스에 -win.zip 과 .sha256 파일이 모두 있어야 해요")
    d = home / "updates"
    d.mkdir(parents=True, exist_ok=True)
    zp = d / f"QTrader-{info['latest']}.zip"
    with requests.get(info["zip"], stream=True, timeout=60) as r:
        r.raise_for_status()
        h = hashlib.sha256()
        with open(zp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                h.update(chunk)
    want = requests.get(info["sha256"], timeout=10).text.split()[0].strip().lower()
    if h.hexdigest() != want:
        zp.unlink(missing_ok=True)
        raise ValueError("내려받은 파일의 지문(SHA-256)이 맞지 않아 업데이트를 멈췄어요")
    stage = d / f"stage-{info['latest']}"
    if stage.exists():
        import shutil
        shutil.rmtree(stage)
    with zipfile.ZipFile(zp) as z:
        for n in z.namelist():  # 압축 밖으로 나가는 경로 막기
            if n.startswith(("/", "\\")) or ".." in Path(n).parts:
                raise ValueError("압축 파일 안 경로가 이상해요")
        z.extractall(stage)
    inner = stage / "QTrader" if (stage / "QTrader" / "QTrader.exe").exists() else stage
    if not (inner / "QTrader.exe").exists():
        raise ValueError("압축 안에 QTrader.exe 가 없어요")
    return inner


def write_script(stage: Path, home: Path) -> Path:
    """프로그램이 꺼진 뒤 파일을 바꾸고 다시 켜는 스크립트."""
    app = Path(sys.executable).resolve().parent
    s = home / "updates" / "apply_update.cmd"
    s.write_text("\r\n".join([
        "@echo off", "chcp 65001 >nul", "echo QTrader 업데이트 중... 창을 닫지 마세요",
        ":wait", 'tasklist /FI "IMAGENAME eq QTrader.exe" | find /I "QTrader.exe" >nul', "if not errorlevel 1 (timeout /t 1 >nul & goto wait)",
        f'robocopy "{stage}" "{app}" /E /R:3 /W:1 /NFL /NDL /NJH /NJS >nul',
        "if errorlevel 8 (echo 파일을 바꾸지 못했어요. 폴더 권한을 확인하세요 & pause & exit /b 1)",
        f'start "" "{app / "QTrader.exe"}"', ""]), encoding="utf-8")
    home.joinpath("updates", "pending.json").write_text(json.dumps({"script": str(s)}), encoding="utf-8")
    return s


def finish_pending(home: Path) -> bool:
    """supervise·desktop 이 부른다: 준비된 업데이트가 있으면 스크립트를 따로 띄우고 True."""
    p = home / "updates" / "pending.json"
    if not p.exists():
        return False
    d = json.loads(p.read_text(encoding="utf-8"))
    p.unlink()
    args = d.get("args") or ["cmd", "/c", d["script"]]
    subprocess.Popen(args, cwd=d.get("cwd"), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
    return True


# A. 내 PC 소스 폴더에서 다시 만들기 (git)
INSTALLER = "QTrader 설치.cmd"


def source_info(home: Path, app_dir: Path | None = None) -> dict | None:
    """설치할 때 적어 둔 소스 폴더와 커밋. 데이터 폴더에 없으면 프로그램 폴더(QTrader.exe 옆)의 기록을 본다.
    폴더가 git 저장소가 아니면 None."""
    if app_dir is None and frozen():
        app_dir = Path(sys.executable).resolve().parent
    for p in [home / "source.json"] + ([app_dir / "source.json"] if app_dir else []):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        src = Path(d.get("src") or "")
        if (src / ".git").exists():
            return d | {"src": str(src)}
    return None


def mark_installed(home: Path, src: Path, run=None, app_dir: Path | None = None) -> dict:
    """설치 스크립트가 부른다: 어느 폴더의 어느 커밋으로 만들었는지 적어 둔다.
    데이터 폴더와 프로그램 폴더(QTrader.exe 옆) 두 곳에 남긴다 (한쪽이 없어져도 업데이트 버튼이 나오게)."""
    rev = _git(src, "rev-parse", "HEAD", run=run)
    d = {"src": str(src.resolve()), "rev": rev, "at": time.strftime("%Y-%m-%d %H:%M")}
    try:  # 업데이트 전 커밋을 남겨 두면, 다시 켰을 때 '이번 업데이트로 바뀐 것'을 보여 줄 수 있다
        old = json.loads((Path(home) / "source.json").read_text(encoding="utf-8"))
        prev = old.get("rev") if old.get("rev") != rev else old.get("prev_rev")
        if prev:
            d["prev_rev"] = prev
    except (OSError, ValueError):
        pass
    for folder in [home] + ([app_dir] if app_dir else []):
        try:
            (Path(folder) / "source.json").write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return d


def _git(src, *args, run=None, timeout: float = 60) -> str:
    """git 을 창 없이, 비밀번호를 묻지 않게 (묻는 창이 뜨면 업데이트 확인이 멈춘다) 부른다."""
    run = run or subprocess.run
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # 한국어 커밋 제목이 윈도우(cp949)에서 깨져 패치노트가 비던 문제: git 출력을 UTF-8 로 받는다
    r = run(["git", "-c", "i18n.logOutputEncoding=utf-8", "-c", "core.quotepath=false", "-C", str(src), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "git 실패").strip().splitlines()[-1][:200])
    return (r.stdout or "").strip()


KIND_KO = {"feat": "새 기능", "fix": "고친 것", "perf": "빨라진 것"}


def patch_notes(log: str) -> list[dict]:
    """git 커밋 제목('- fix(market): 등락률을 … (#28)')을 쉬운 패치노트로: [{kind: '고친 것', text: '등락률을 …'}].
    새 기능 → 고친 것 → 빨라진 것 순서. 문서·시험·정리 커밋은 다른 게 없을 때만 '손본 것'으로."""
    import re
    rows, rest = [], []
    for line in (log or "").splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or line.lower().startswith("merge"):
            continue
        m = re.match(r"^(\w+)(?:\([^)]*\))?!?:\s*(.+)$", line)
        kind, text = (m.group(1).lower(), m.group(2)) if m else ("", line)
        text = re.sub(r"\s*\(#\d+\)\s*$", "", text).strip()
        (rows if kind in KIND_KO else rest).append({"kind": KIND_KO.get(kind, "손본 것"), "text": text})
    order = list(KIND_KO.values())
    rows.sort(key=lambda r: order.index(r["kind"]))
    return rows or rest


def just_updated(info: dict | None, store, run=None) -> dict | None:
    """방금 업데이트했으면 그때 바뀐 것 (한 번 보고 '확인'을 누르면 다시 안 보인다)."""
    if not info or not info.get("prev_rev") or not info.get("rev") or store is None:
        return None
    if store.get("update_seen") == info["rev"]:
        return None
    try:
        log = _git(info["src"], "log", "--format=- %s", "-n", "30", f"{info['prev_rev']}..{info['rev']}", run=run)
    except Exception:
        return None
    changes = patch_notes(log)
    return {"rev": info["rev"][:7], "at": info.get("at"), "changes": changes} if changes else None


def check_git(info: dict, store=None, force: bool = False, run=None) -> dict:
    """설치한 커밋과 GitHub main 을 비교한다. 6시간에 한 번만 묻는다."""
    src, rev = info["src"], info.get("rev") or ""
    base = {"mode": "git", "current": __version__, "current_rev": rev[:7], "available": False, "installed": frozen(), "repo": "git"}
    if os.environ.get("QTRADER_OFFLINE") and run is None:
        return base | {"reason": "오프라인"}
    if store is not None and not force:
        c = json.loads(store.get("update_check_git") or "{}")
        if c.get("rev") == rev and time.time() - c.get("at", 0) < 6 * 3600:
            return base | c.get("res", {})
    try:
        _git(src, "fetch", "-q", "origin", "main", run=run, timeout=90)
        latest = _git(src, "rev-parse", "origin/main", run=run)
        behind = int(_git(src, "rev-list", "--count", f"{rev}..origin/main", run=run) or 0) if rev else 1
        notes = _git(src, "log", "--format=- %s", "-n", "30", f"{rev}..origin/main", run=run) if rev else ""
    except Exception as e:
        return base | {"reason": f"확인 실패: {e}"}
    res = {"latest": latest[:7], "behind": behind, "notes": notes, "changes": patch_notes(notes), "available": behind > 0}
    if store is not None:
        store.set("update_check_git", json.dumps({"rev": rev, "at": time.time(), "res": res}))
    return base | res


def write_git_pending(info: dict, home: Path) -> Path:
    """프로그램이 꺼진 뒤 'QTrader 설치.cmd --update' 가 최신 코드 받기 -> 다시 만들기 -> 설치 -> 다시 켜기를 한다."""
    src = Path(info["src"])
    script = src / INSTALLER
    if not script.exists():
        raise ValueError(f"{script} 가 없어요. 소스 폴더를 확인하세요")
    d = home / "updates"
    d.mkdir(parents=True, exist_ok=True)
    (d / "pending.json").write_text(json.dumps({"args": ["cmd", "/c", str(script), "--update"], "cwd": str(src)}), encoding="utf-8")
    return script
