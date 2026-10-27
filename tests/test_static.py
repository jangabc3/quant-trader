"""화면 코드 품질 검사 (브라우저 없이).

- 스크립트끼리 최상위 const/let/class 가 겹치면 뒤 스크립트 전체가 멈춘다 (예전 'cmp' 충돌로 수익 리포트가 통째로 안 떴다)
- 함수 이름이 다른 파일에서 겹치면 나중 것이 조용히 이긴다 (예전 loadCompare 충돌로 '지난 회의와 비교'가 엉뚱하게 떴다)
- 화면이 부르는 /api/... 주소가 서버에 실제로 있는지
- 화면이 찾는 #아이디가 HTML 이나 스크립트가 만드는 HTML 안에 있는지
- 문법 오류 (node 가 있으면)
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "qtrader" / "web" / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
FILES = re.findall(r'src="/static/([\w-]+\.js)', HTML)
SRC = {f: (STATIC / f).read_text(encoding="utf-8") for f in FILES}


def _dups(pattern):
    seen, dup = {}, []
    for f, s in SRC.items():
        for n in re.findall(pattern, s, re.M):
            if n in seen and seen[n] != f:
                dup.append((n, seen[n], f))
            seen.setdefault(n, f)
    return seen, dup


def test_no_duplicate_top_level_names():
    seen, dup = _dups(r"^(?:const|let|class)\s+([A-Za-z_$][\w$]*)")
    assert len(seen) > 30 and not dup, dup


def test_no_function_defined_in_two_files():
    _, dup = _dups(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")
    assert not dup, dup


def test_api_paths_exist():
    from qtrader.web.app import create_app
    routes = [r.path for r in create_app().routes if getattr(r, "path", "").startswith("/api/")]
    pats = [re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", p) + "$") for p in routes]
    missing = set()
    for f, s in SRC.items():
        for m in re.finditer(r"""["'`](/api/[^"'`?\s]*)""", s):
            path = re.sub(r"\$\{[^}]*\}", "X", m.group(1))  # ${...} 는 한 칸으로 본다
            if path.endswith("/"):
                path += "X"
            if not any(p.match(path) for p in pats):
                missing.add((f, m.group(1)))
    assert not missing, sorted(missing)


def test_ids_used_by_scripts_exist():
    known = set(re.findall(r'id="([\w-]+)"', HTML))
    for s in SRC.values():
        known |= set(re.findall(r'id="([\w-]+)"', s)) | set(re.findall(r"\.id\s*=\s*[\"']([\w-]+)", s))
        known |= set(re.findall(r'kpi\([^\n]*"([\w-]+)"\)', s))  # kpi(..., "k-eq") 처럼 아이디를 넘겨 만드는 칸
    used = set()
    for s in SRC.values():
        used |= set(re.findall(r"""\$\(\s*["'`]#([\w-]+)["'`]\s*\)""", s))
        used |= set(re.findall(r"""getElementById\(\s*["'`]([\w-]+)["'`]""", s))
    assert not (used - known), sorted(used - known)


@pytest.mark.skipif(not shutil.which("node"), reason="node 가 없으면 문법 검사는 건너뜀")
def test_js_syntax():
    bad = []
    for f in FILES:
        r = subprocess.run(["node", "--check", str(STATIC / f)], capture_output=True, text=True)
        if r.returncode:
            bad.append((f, r.stderr[-300:]))
    assert not bad, bad
