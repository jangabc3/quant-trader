@echo off
chcp 65001 >nul
title QTrader
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행: 필요한 프로그램을 설치합니다. 잠시 기다려 주세요...
  python -m venv .venv || (echo Python 3.11 이상을 먼저 설치해 주세요. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -e . || (echo 설치에 실패했습니다. & pause & exit /b 1)
)
rem 프로그램 창 도구(pywebview)가 없으면 한 번 설치 (예전에 만든 .venv 에는 없다)
".venv\Scripts\python.exe" -c "import webview" 2>nul || ".venv\Scripts\python.exe" -m pip install -q -e .
".venv\Scripts\python.exe" -m qtrader app
pause
