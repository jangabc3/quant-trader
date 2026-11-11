@echo off
rem 두 번 누르면 QTrader.exe 를 만들어 D:\QTrader 에 설치하고 바탕화면에 바로가기를 만든다.
rem 키·설정·매매 기록은 %APPDATA%\QTrader 에 따로 있어서 그대로 이어진다.
rem 프로그램의 '지금 업데이트'는 이 파일을 --update 로 부른다: 프로그램이 꺼지기를 기다렸다가 같은 순서로 다시 설치하고 켠다.
chcp 65001 >nul
title QTrader 설치
rem 최신 코드를 받다가 이 파일 자체가 바뀌어도 멈추지 않게, 임시 폴더에 복사본을 만들어 그걸로 돈다
rem (복사본 이름은 매번 달라서, 새 설치 파일로 다시 시작할 때 돌고 있는 복사본을 덮어쓰지 않는다)
if /i "%~1"=="--run" goto run
set "QT_TMP=%TEMP%\qtrader_install_%RANDOM%%RANDOM%.cmd"
copy /y "%~f0" "%QT_TMP%" >nul
call "%QT_TMP%" --run "%~dp0" %1
exit /b
:run
cd /d "%~2"
set "DEST=D:\QTrader"
set "MODE=%~3"

if not exist D:\ (echo D 드라이브가 없어요. & pause & exit /b 1)
if /i not "%MODE%"=="--update" goto checks
echo 업데이트: QTrader 가 꺼지기를 기다리는 중...
set /a N=0
:wait
set "BUSY="
tasklist /FI "IMAGENAME eq QTrader.exe" | find /I "QTrader.exe" >nul && set "BUSY=1"
netstat -ano | find ":8765" | find "LISTENING" >nul && set "BUSY=1"
if not defined BUSY goto build
set /a N+=1
if %N% GEQ 90 (echo QTrader 가 꺼지지 않아 업데이트를 멈췄어요. 작업 관리자에서 QTrader 를 끄고 이 파일을 다시 실행해 주세요. & pause & exit /b 1)
timeout /t 1 /nobreak >nul
goto wait

:checks
tasklist /FI "IMAGENAME eq QTrader.exe" | find /I "QTrader.exe" >nul && (echo 켜져 있는 QTrader 를 먼저 닫고 다시 실행해 주세요. & pause & exit /b 1)
netstat -ano | find ":8765" | find "LISTENING" >nul && (echo 'QTrader 실행.cmd' 로 켠 창이 아직 열려 있어요. 그 창을 닫고 다시 실행해 주세요. & pause & exit /b 1)

:build
echo.
echo [1/4] 최신 코드 받기
git checkout -q main && git pull -q --ff-only || goto pullfail
rem 받은 코드에서 이 설치 파일이 바뀌었으면, 새 설치 파일로 다시 시작한다 (옛 순서로 한 번 더 돌지 않게)
fc /b "%~f0" "%CD%\QTrader 설치.cmd" >nul 2>&1 || (echo     설치 파일이 새로 바뀌어 새 순서로 다시 시작합니다. & call "%CD%\QTrader 설치.cmd" %MODE% & exit /b)

:afterpull
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
rem 어느 폴더의 어느 커밋으로 만들었는지 적어 둔다 (프로그램이 이것과 GitHub main 을 비교해 새 버전을 알린다)
".venv\Scripts\python.exe" -m qtrader mark-installed --src "%CD%" --app "%DEST%" || echo     설치 기록을 남기지 못했어요. 이 창을 찍어서 보내 주세요 (프로그램은 그대로 쓸 수 있어요).

echo.
echo 설치 끝! 바탕화면의 QTrader 아이콘으로 켜면 돼요. 지금 바로 켭니다.
echo 다음부터는 프로그램의 '지금 업데이트' 버튼으로 새 버전을 받을 수 있어요.
start "" "%DEST%\QTrader.exe"
if /i not "%MODE%"=="--update" pause
exit /b 0

:pullfail
echo 최신 코드를 받지 못했어요 (인터넷 연결이나 GitHub 로그인을 확인해 주세요).
if /i "%MODE%"=="--update" (echo 예전 버전으로 다시 켭니다. & start "" "%DEST%\QTrader.exe" & pause & exit /b 1)
echo 지금 있는 코드로 만듭니다.
goto afterpull
