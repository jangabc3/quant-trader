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


def test_html_ids_unique():
    ids = re.findall(r'\bid="([\w-]+)"', HTML)
    dup = sorted({i for i in ids if ids.count(i) > 1})
    assert not dup, dup


# 브라우저 기본 함수와 글자 속 CSS 함수(rgba(), translateX() 등) — 정의 안 된 것으로 치지 않는다
_KNOWN = set("""if for while switch catch function return typeof fetch setTimeout setInterval clearInterval clearTimeout Number String
Boolean Array Date parseInt parseFloat isNaN isFinite alert confirm prompt encodeURIComponent decodeURIComponent requestAnimationFrame
cancelAnimationFrame structuredClone Promise Set Map Error anime getComputedStyle new await async super import RegExp Symbol Intl URL
URLSearchParams EventSource Notification CustomEvent Event Audio Image FormData Blob AbortController queueMicrotask btoa atob matchMedia
PointerEvent TextDecoder TextEncoder rgba rgb hsl var url translate translateX translateY scale rotate minmax repeat bezier not calc Claude""".split())


def test_called_functions_are_defined():
    """설정 화면을 다시 짤 때 refreshKeyState 를 지워서 키 종류를 바꿔도 칸이 안 바뀌던 일이 다시 없게."""
    src = "\n".join(SRC.values())
    defined = set(re.findall(r"\b(?:function\s+|const\s+|let\s+|var\s+|class\s+)([A-Za-z_$][\w$]*)", src))
    defined |= set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", src))
    for m in re.finditer(r"\(([^()]*)\)\s*=>|function\s*[\w$]*\s*\(([^()]*)\)|([A-Za-z_$][\w$]*)\s*=>", src):
        for g in m.groups():
            if g:
                defined |= set(re.findall(r"[A-Za-z_$][\w$]*", g))
    for g in re.findall(r"(?:const|let)\s*[\[{]([^\]}]*)[\]}]\s*=", src):
        defined |= set(re.findall(r"[A-Za-z_$][\w$]*", g))
    called = set(re.findall(r"(?<![\w.$])([A-Za-z_$][\w$]*)\s*\(", src))
    assert not (called - defined - _KNOWN), sorted(called - defined - _KNOWN)


def test_no_native_dialogs():
    """앱 안 브라우저에서는 confirm·prompt·alert 가 보이지 않고 바로 '취소'된다 (자동매매 시작 버튼이 안 눌리던 원인). 화면 안 창 ask() 를 쓴다."""
    bad = [(f, m.group(0)) for f, s in SRC.items() for m in re.finditer(r"(?<![\w.])(confirm|prompt|alert)\(", s)]
    assert not bad, bad
