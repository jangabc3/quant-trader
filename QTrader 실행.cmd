@echo off
chcp 65001 >nul
title QTrader
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 처음 실행: 필요한 프로그램을 설치합니다. 잠시 기다려 주세요...
  python -m venv .venv || (echo Python 3.11 이상을 먼저 설치해 주세요. & pause & exit /b 1)
  ".venv\Scripts\python.exe" -m pip install -q -e . || (echo 설치에 실패했습니다. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m qtrader supervise
pause
