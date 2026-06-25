@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

echo ====================================================
echo  Run daily pipeline once
echo  (build_dataset -^> Claude analysis -^> email)
echo ====================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0src\run_daily.ps1"

echo.
echo Pipeline finished (see today's log under logs\).
