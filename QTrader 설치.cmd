@echo off
rem 두 번 누르면 QTrader.exe 를 만들어 D:\QTrader 에 설치하고 바탕화면에 바로가기를 만든다.
rem 키·설정·매매 기록은 %APPDATA%\QTrader 에 따로 있어서 그대로 이어진다.
chcp 65001 >nul
title QTrader 설치
rem 최신 코드를 받다가 이 파일 자체가 바뀌어도 멈추지 않게, 임시 폴더에 복사본을 만들어 그걸로 돈다
if /i not "%~1"=="--run" (
  copy /y "%~f0" "%TEMP%\qtrader_install.cmd" >nul
  call "%TEMP%\qtrader_install.cmd" --run "%~dp0"
  exit /b
)
cd /d "%~2"
set "DEST=D:\QTrader"

if not exist D:\ (echo D 드라이브가 없어요. & pause & exit /b 1)
tasklist /FI "IMAGENAME eq QTrader.exe" | find /I "QTrader.exe" >nul && (echo 켜져 있는 QTrader 를 먼저 닫고 다시 실행해 주세요. & pause & exit /b 1)
netstat -ano | find ":8765" | find "LISTENING" >nul && (echo 'QTrader 실행.cmd' 로 켠 창이 아직 열려 있어요. 그 창을 닫고 다시 실행해 주세요. & pause & exit /b 1)

echo.
echo [1/4] 최신 코드 받기
git checkout -q main && git pull -q --ff-only || echo     최신 코드를 받지 못해 지금 있는 코드로 만듭니다.

echo [2/4] 필요한 것 설치
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv || (echo Python 3.11 이상을 먼저 설치해 주세요. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip install -q -e . || (echo 설치에 실패했어요. 이 창을 찍어서 보내 주세요. & pause & exit /b 1)

echo [3/4] 프로그램 만들기 (몇 분 걸려요)
call packaging\build_exe.cmd >nul || (echo 프로그램 만들기에 실패했어요. packaging\build_exe.cmd 를 직접 실행해 오류를 찍어서 보내 주세요. & pause & exit /b 1)

echo [4/4] %DEST% 에 설치
robocopy dist\QTrader "%DEST%" /E /R:2 /W:1 /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (echo %DEST% 에 복사하지 못했어요. & pause & exit /b 1)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\QTrader.lnk'); $s.TargetPath='D:\QTrader\QTrader.exe'; $s.WorkingDirectory='D:\QTrader'; $s.Save()" >nul 2>&1

echo.
echo 설치 끝! 바탕화면의 QTrader 아이콘으로 켜면 돼요. 지금 바로 켭니다.
echo 다음부터는 이 파일을 다시 두 번 누르면 새 버전으로 바뀌어요.
start "" "%DEST%\QTrader.exe"
pause
