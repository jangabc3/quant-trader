@echo off
rem 새 버전 배포: 1) qtrader\__init__.py 의 __version__ 을 올리고 2) 이 파일을 실행 3) GitHub Releases 에 올리기
chcp 65001 >nul
cd /d "%~dp0\.."
call packaging\build_exe.cmd || exit /b 1
".venv\Scripts\python.exe" packaging\release.py
