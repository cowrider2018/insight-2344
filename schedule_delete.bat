@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "TASK=CMoney_2344_Daily"
set "TASK_PRE=CMoney_2344_Intraday"

echo ====================================================
echo  Delete scheduled tasks: %TASK_PRE%, %TASK%
echo ====================================================
echo.

set "FAILED="
schtasks /Delete /TN "%TASK_PRE%" /F || set "FAILED=1"
schtasks /Delete /TN "%TASK%" /F     || set "FAILED=1"

if defined FAILED (
    echo.
    echo [NOTE] At least one delete failed; the task may not exist, or run as Administrator.
    exit /b 1
)

echo.
echo Scheduled tasks deleted.
