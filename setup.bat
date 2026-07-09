@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

echo ====================================================
echo  2344 Winbond pre-market analysis - one-stop build
echo  (env + data backfill + weights + Gmail auth)
echo ====================================================
echo.

echo [1/7] Create virtual environment (.venv) and install packages
if not exist "%PY%" (
    python -m venv .venv
    if errorlevel 1 goto :err
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo [2/7] Install Playwright Chromium (for news scraping)
"%PY%" -m playwright install chromium
if errorlevel 1 goto :err

echo.
echo [3/7] Initialize timeline database data\market.db
"%PY%" src\timeline_db.py --init
if errorlevel 1 goto :err

echo.
echo [4/7] Backfill snapshots (news/chips/revenue), daily candles (Fugle),
echo       1-min intraday (Fugle), broker branches (Fubon DJ), overnight US (Yahoo)
"%PY%" src\ingest.py --backfill-json
"%PY%" src\ingest.py --backfill-candles
"%PY%" src\ingest.py --backfill-intraday
"%PY%" src\ingest.py --backfill-branches
"%PY%" src\ingest.py --backfill-us

echo.
echo [5/7] Authorize Gmail (opens browser once) -^> token.json
echo       Do this now while you are here; the next steps run unattended.
"%PY%" -c "import sys; sys.path.insert(0, 'src'); import send_email; send_email._gmail_service(); print('Gmail authorized -> token.json')"
if errorlevel 1 goto :err

echo.
echo [6/7] Backfill historical chips + build branch behavioral model
echo       (chip backfill calls TWSE day by day; may take several minutes)
"%PY%" src\ingest.py --backfill-chips
if errorlevel 1 goto :err
"%PY%" src\branch_model.py

echo.
echo [7/7] Backtest -^> data\weights.json + validate news -^> data\news_patterns.json
"%PY%" src\backtest.py --start 2025-07-01
if errorlevel 1 goto :err
"%PY%" src\validate_news.py --start 2025-07-01

echo.
echo ====================================================
echo  Build complete. The system is ready to run.
echo.
echo  Notes:
echo   - Create .env in the project root first (see README);
echo     at least FUGLE_MARKETDATA_API_KEY is required for daily candles.
echo   - Email needs credentials.json / token.json (Gmail OAuth).
echo.
echo  Next steps:
echo   run_once.bat           Run the daily pipeline once
echo   schedule_create.bat    Create the daily 06:00 scheduled task
echo.
echo  Advanced (see advanced\): re-tune params (calibrate.bat),
echo   refresh history/weights (backfill_history.bat), per-symbol
echo   strategy build (build_stock.bat) / daily card (daily_stock.bat).
echo ====================================================
goto :eof

:err
echo.
echo [ERROR] Setup failed, see messages above.
exit /b 1
