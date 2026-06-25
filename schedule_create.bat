@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "TASK=CMoney_2344_Daily"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File '%~dp0src\run_daily.ps1'"

echo ====================================================
echo  Create daily 06:00 scheduled task: %TASK%
echo ====================================================
echo.

schtasks /Create /TN "%TASK%" /TR "%PS%" /SC DAILY /ST 06:00 /F
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create task. If access is denied, run this file as Administrator.
    exit /b 1
)

echo.
echo Scheduled task created; runs automatically every day at 06:00.
echo Test now:   schtasks /Run /TN "%TASK%"   or run run_once.bat directly
echo Query task: schtasks /Query /TN "%TASK%"
