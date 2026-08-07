@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo ====================================================
echo  Backfill data gaps up to today
echo    futures_oi / broker_branches / branch_wf / xs.db
echo ====================================================
echo.
echo   backfill.bat              last 60 trading days (default)
echo   backfill.bat --full       all history
echo   backfill.bat --days 120   custom lookback
echo   backfill.bat --check      report gaps only, no fetching
echo.

set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python

"%PY%" "%~dp0src\backfill.py" %*

echo.
echo Backfill finished.
