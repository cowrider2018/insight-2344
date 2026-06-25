@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "TASK=CMoney_2344_Daily"

echo ====================================================
echo  Delete scheduled task: %TASK%
echo ====================================================
echo.

schtasks /Delete /TN "%TASK%" /F
if errorlevel 1 (
    echo.
    echo [NOTE] Delete failed; task may not exist, or run as Administrator.
    exit /b 1
)

echo.
echo Scheduled task %TASK% deleted.
