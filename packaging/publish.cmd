@echo off
rem 새 버전 배포 한 번에: packaging\publish.cmd "바뀐 점" [--minor ^| --version 1.0.0] [--repo 아이디/저장소]
chcp 65001 >nul
cd /d "%~dp0\.."
".venv\Scripts\python.exe" packaging\publish.py %*
