"""새 버전 배포를 한 번에: 버전 올리기 -> QTrader.exe 만들기 -> 압축·지문 -> GitHub Releases 올리기 -> 버전 커밋.

    packaging\\publish.cmd "바뀐 점 한 줄"            (끝자리 +1, 예: 0.1.0 -> 0.1.1)
    packaging\\publish.cmd "바뀐 점" --minor           (가운데 +1, 예: 0.1.3 -> 0.2.0)
    packaging\\publish.cmd "바뀐 점" --version 1.0.0   (직접 정하기)

올릴 곳: --repo 아이디/저장소, 없으면 QTrader 설정의 '업데이트 저장소'(update.repo).
업데이트 저장소는 공개(Public)여야 한다 (프로그램이 로그인 없이 새 버전을 확인한다). 없으면 공개 저장소를 만든다.
GitHub CLI(gh)가 필요하다: https://cli.github.com 설치 뒤 한 번 'gh auth login'.
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "qtrader" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"


def bump(v: str, part: str) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return {"major": f"{a + 1}.0.0", "minor": f"{a}.{b + 1}.0", "patch": f"{a}.{b}.{c + 1}"}[part]


def current() -> str:
    return re.search(r'__version__ = "([\d.]+)"', INIT.read_text(encoding="utf-8")).group(1)


def set_version(v: str) -> None:
    INIT.write_text(re.sub(r'__version__ = "[\d.]+"', f'__version__ = "{v}"', INIT.read_text(encoding="utf-8")), encoding="utf-8")
    PYPROJECT.write_text(re.sub(r'(?m)^version = "[\d.]+"', f'version = "{v}"', PYPROJECT.read_text(encoding="utf-8"), count=1),
                         encoding="utf-8")


def run(*cmd, check=True, **kw) -> subprocess.CompletedProcess:
    print(">", " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=ROOT, check=check, **kw)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="QTrader 새 버전 배포")
    ap.add_argument("notes", help="바뀐 점 (사용자 업데이트 알림에 그대로 보여요)")
    ap.add_argument("--version", help="버전을 직접 정하기 (예: 1.0.0)")
    ap.add_argument("--minor", action="store_true", help="가운데 자리를 올리기")
    ap.add_argument("--repo", help="올릴 공개 저장소 (아이디/저장소)")
    a = ap.parse_args(argv)

    if not shutil.which("gh"):
        print("GitHub CLI(gh)가 없어요. https://cli.github.com 에서 설치하고 'gh auth login' 을 한 번 해 주세요.")
        return 1
    repo = a.repo
    if not repo:
        sys.path.insert(0, str(ROOT))
        from qtrader import config
        repo = ((config.load().get("update") or {}).get("repo") or "").strip()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo or ""):
        print("올릴 저장소를 모르겠어요. --repo 아이디/qtrader-releases 로 주거나 설정의 '업데이트 저장소'를 채워 주세요.")
        return 1
    if run("git", "status", "--porcelain", capture_output=True, text=True).stdout.strip():
        print("커밋하지 않은 변경이 있어요. 먼저 커밋하거나 되돌린 뒤 다시 실행하세요 (배포본과 코드가 같아야 해요).")
        return 1

    old = current()
    new = a.version or bump(old, "minor" if a.minor else "patch")
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        print(f"버전 형식이 이상해요: {new} (예: 1.2.3)")
        return 1
    print(f"버전 {old} -> {new}, 올릴 곳 {repo}")
    set_version(new)
    try:
        run("cmd", "/c", str(ROOT / "packaging" / "build_exe.cmd"))
        run(sys.executable, str(ROOT / "packaging" / "release.py"))
    except subprocess.CalledProcessError:
        set_version(old)
        print("만들기에 실패해 버전을 되돌렸어요. 위 오류를 확인하세요.")
        return 1

    if run("gh", "repo", "view", repo, check=False, capture_output=True).returncode != 0:
        run("gh", "repo", "create", repo, "--public", "--description", "QTrader 업데이트 파일 (코드는 따로 보관)")
    z = ROOT / "dist" / f"QTrader-{new}-win.zip"
    run("gh", "release", "create", f"v{new}", str(z), f"{z}.sha256", "--repo", repo, "--title", f"QTrader {new}", "--notes", a.notes)

    run("git", "add", str(INIT), str(PYPROJECT))
    run("git", "commit", "-m", f"버전 {new} 배포: {a.notes}")
    run("git", "tag", f"v{new}")
    if run("git", "push", "--follow-tags", check=False).returncode != 0:
        print("코드 저장소 올리기(git push)는 실패했어요. 나중에 'git push --follow-tags' 를 해 주세요.")
    print(f"\n끝났어요. QTrader {new} 이(가) {repo} 에 올라갔어요. 각 PC 의 QTrader 가 6시간 안에 알려 줘요 (설정에서 '지금 확인'은 바로).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
