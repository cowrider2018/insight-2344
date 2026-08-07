@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "TASK=CMoney_2344_Daily"
set "TASK_PRE=CMoney_2344_Intraday"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File '%~dp0src\run_daily.ps1'"
set "PS_PRE=powershell -NoProfile -ExecutionPolicy Bypass -File '%~dp0src\run_intraday.ps1'"

echo ====================================================
echo  Create scheduled tasks
echo    05:30  %TASK_PRE%  (refresh daily K + 1-min bars)
echo    06:00  %TASK%      (fetch - analyze - email)
echo ====================================================
echo.

schtasks /Create /TN "%TASK_PRE%" /TR "%PS_PRE%" /SC DAILY /ST 05:30 /F
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create task %TASK_PRE%. If access is denied, run this file as Administrator.
    exit /b 1
)

schtasks /Create /TN "%TASK%" /TR "%PS%" /SC DAILY /ST 06:00 /F
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create task %TASK%. If access is denied, run this file as Administrator.
    exit /b 1
)

echo.
echo Scheduled tasks created; they run automatically every day at 05:30 and 06:00.
echo Test now:   schtasks /Run /TN "%TASK%"   or run run_once.bat directly
echo Query task: schtasks /Query /TN "%TASK%"
