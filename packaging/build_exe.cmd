@echo off
rem QTrader.exe 만들기 (다른 사용자에게 나눠 줄 때). 결과: dist\QTrader\QTrader.exe
rem 받는 사람은 파이썬이 없어도 됩니다. 폴더째 압축해서 전달하세요.
chcp 65001 >nul
cd /d "%~dp0\.."
".venv\Scripts\python.exe" -m pip install -q "pyinstaller>=6.10" || exit /b 1
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --name QTrader --console ^
  --add-data "qtrader\web\static;qtrader\web\static" ^
  --collect-submodules uvicorn --collect-submodules keyring --hidden-import keyring.backends.Windows ^
  packaging\qtrader_app.py
copy /y NOTICE dist\QTrader\ >nul
copy /y README.md dist\QTrader\ >nul
echo.
echo 완료: dist\QTrader\QTrader.exe
