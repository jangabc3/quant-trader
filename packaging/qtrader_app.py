"""설치 파일(QTrader.exe) 진입점. 인자가 없으면 화면(serve)을 연다."""
import sys

from qtrader.cli import main

if __name__ == "__main__":
    main(sys.argv[1:] or ["serve"])
