"""화면 스크립트끼리 최상위 이름이 겹치면 뒤 스크립트 전체가 멈춘다 (예전 'cmp' 충돌). 그런 일이 다시 없게 검사."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "qtrader" / "web" / "static"


def test_no_duplicate_top_level_names():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    seen, dup = {}, []
    for f in re.findall(r'src="/static/([\w-]+\.js)', html):
        for n in re.findall(r"^(?:const|let|class)\s+([A-Za-z_$][\w$]*)", (STATIC / f).read_text(encoding="utf-8"), re.M):
            if n in seen and seen[n] != f:
                dup.append((n, seen[n], f))
            seen.setdefault(n, f)
    assert len(seen) > 30 and not dup, dup
