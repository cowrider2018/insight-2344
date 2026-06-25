@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM 對齊 TUNING.md 基準視窗（backtest.py 預設為 2025-01-01，這裡改用策略一致的 2025-07-01）
set ARGS=%*
if "%ARGS%"=="" set ARGS=--start 2025-07-01

echo ====================================================
echo  Refresh history + recompute 6-factor weights using CURRENT calibrated params
echo  = TUNING.md Step A (backtest part) + Step B (news) + Step C (OOS honesty)
echo  To RE-TUNE the scoring params instead, run calibrate.bat.
echo ====================================================
echo.
echo Note: chip backfill calls TWSE day by day; may take several minutes.
echo Using args: %ARGS%
echo.

echo [1/4] Backfill historical chips (auto span from candles)
"%PY%" src\ingest.py --backfill-chips
if errorlevel 1 goto :err

echo.
echo [1b/4] Backfill 1-min intraday (Fugle) + broker branches: latest + ~6mo history (Fubon DJ)
"%PY%" src\ingest.py --backfill-intraday
"%PY%" src\ingest.py --backfill-branches
"%PY%" src\ingest.py --backfill-branches-history

echo.
echo [1c/4] Build branch walk-forward behavioral model (per-broker weighted edge) -^> branch_wf table + data\branch_profiles.json
"%PY%" src\branch_model.py

echo.
echo [2/4] Backtest with current params -^> data\weights.json + reports\backtest_*.md
"%PY%" src\backtest.py %ARGS%
if errorlevel 1 goto :err

echo.
echo [3/4] Validate news pattern polarity -^> data\news_patterns.json
"%PY%" src\validate_news.py %ARGS%

echo.
echo [4/4] Out-of-sample honesty check (expect OOS ^>= baseline ~47%%, not far below in-sample)
"%PY%" src\oos_check.py %ARGS%

echo.
echo Done. data\weights.json (balanced) + data\news_patterns.json updated; see reports\ for diagnostics.
goto :eof

:err
echo.
echo [ERROR] Run failed, see messages above.
exit /b 1
