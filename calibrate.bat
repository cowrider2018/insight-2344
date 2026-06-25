@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM 對齊 TUNING.md：未帶參數時用策略基準視窗（與 oos_check/validate_news 一致）
set ARGS=%*
if "%ARGS%"=="" set ARGS=--start 2025-07-01 --rounds 2

echo ====================================================
echo  TUNING.md Step A + Step C  (6-factor: technical/chips/news/fundamental/micron/sox)
echo  A) coordinate-ascent calibrate score_params.json, then full-grid balanced weights
echo  C) out-of-sample honesty check (no look-ahead / no overfit)
echo ====================================================
echo.
echo Using args: %ARGS%
echo (override e.g. calibrate.bat --start 2025-07-01 --end 2026-06-23 --rounds 2)
echo.

echo [0/2] Build branch walk-forward behavioral model (per-broker weighted edge) -^> branch_wf table + data\branch_profiles.json
"%PY%" src\branch_model.py

echo.
echo [1/2] Calibrate params -^> data\score_params.json, full grid -^> data\weights.json (balanced+calibrated)
"%PY%" src\calibrate.py %ARGS%
if errorlevel 1 goto :err

echo.
echo [2/2] Out-of-sample check (train-calibrate, test-validate); expect OOS ^>= baseline ~47%%
"%PY%" src\oos_check.py %ARGS%

echo.
echo Done. data\score_params.json + data\weights.json updated.
echo See reports\backtest_*.md for per-signal direction hit-rate diagnostics.
goto :eof

:err
echo.
echo [ERROR] Calibration failed, see messages above.
exit /b 1
