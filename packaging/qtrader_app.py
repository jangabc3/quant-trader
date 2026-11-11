"""설치 파일(QTrader.exe) 진입점. 인자가 없으면 프로그램 창(app)을 연다."""
import sys

from qtrader.cli import main


def _quiet_console() -> None:
    """창 프로그램(--windowed)은 까만 창이 없어 sys.stdout/stderr 가 None 이다.
    그대로 두면 print·서버 기록이 실패하므로 데이터 폴더의 qtrader.log 로 보낸다."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from qtrader import config
    log = open(config.home() / "qtrader.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


if __name__ == "__main__":
    _quiet_console()
    main(sys.argv[1:] or ["app"])
