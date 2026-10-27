"""배포용 압축 만들기: dist\\QTrader 를 QTrader-<버전>-win.zip 으로 묶고 SHA-256 파일을 만든다.
먼저 packaging\\build_exe.cmd 로 빌드하세요. 그다음 GitHub Releases 에 태그 v<버전> 으로 두 파일을 올리면 됩니다.
gh CLI 가 있으면:  gh release create v<버전> dist/QTrader-<버전>-win.zip dist/QTrader-<버전>-win.zip.sha256 --notes "바뀐 점"
"""
import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from qtrader import __version__  # noqa: E402

src = ROOT / "dist" / "QTrader"
if not (src / "QTrader.exe").exists():
    sys.exit("dist\\QTrader\\QTrader.exe 가 없어요. 먼저 packaging\\build_exe.cmd 를 실행하세요")
out = ROOT / "dist" / f"QTrader-{__version__}-win.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for f in src.rglob("*"):
        if f.is_file():
            z.write(f, Path("QTrader") / f.relative_to(src))
h = hashlib.sha256(out.read_bytes()).hexdigest()
(out.parent / (out.name + ".sha256")).write_text(f"{h}  {out.name}\n", encoding="utf-8")
print(f"만들었어요: {out}\nSHA-256: {h}")
print(f'올리기: gh release create v{__version__} "{out}" "{out}.sha256" --notes "바뀐 점을 적으세요"')
