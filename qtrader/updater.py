"""자동 업데이트 (GitHub Releases).

배포하는 쪽 (개발자):
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
    """supervise 가 부른다: 준비된 업데이트가 있으면 스크립트를 따로 띄우고 True."""
    p = home / "updates" / "pending.json"
    if not p.exists():
        return False
    script = json.loads(p.read_text(encoding="utf-8"))["script"]
    p.unlink()
    subprocess.Popen(["cmd", "/c", script], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0), close_fds=True)
    return True
