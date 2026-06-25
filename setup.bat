@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

set "PY=.venv\Scripts\python.exe"

echo ====================================================
echo  2344 Winbond pre-market analysis - environment setup
echo ====================================================
echo.

echo [1/6] Create virtual environment (.venv) and install packages
if not exist "%PY%" (
    python -m venv .venv
    if errorlevel 1 goto :err
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo [2/6] Install Playwright Chromium (for news scraping)
"%PY%" -m playwright install chromium
if errorlevel 1 goto :err

echo.
echo [3/6] Initialize timeline database data\market.db
"%PY%" src\timeline_db.py --init
if errorlevel 1 goto :err

echo.
echo [4/6] Backfill news/chips/revenue/candles from existing snapshots
"%PY%" src\ingest.py --backfill-json

echo.
echo [5/6] Backfill daily candles (Fugle), 1-min intraday (Fugle), broker branches (Fubon DJ) and overnight US (Yahoo)
"%PY%" src\ingest.py --backfill-candles
"%PY%" src\ingest.py --backfill-intraday
"%PY%" src\ingest.py --backfill-branches
"%PY%" src\ingest.py --backfill-us

echo.
echo [6/6] Authorize Gmail (opens browser once) -^> token.json
"%PY%" -c "import sys; sys.path.insert(0, 'src'); import send_email; send_email._gmail_service(); print('Gmail authorized -> token.json')"
if errorlevel 1 goto :err

echo.
echo ====================================================
echo  Environment setup complete.
echo.
echo  Notes:
echo   - Create .env in the project root first (see README);
echo     at least FUGLE_MARKETDATA_API_KEY is required for daily candles.
echo   - Email needs credentials.json / token.json (Gmail OAuth).
echo.
echo  Next steps (optional):
echo   backfill_history.bat   Backfill historical chips + run backtest -^> data\weights.json
echo   run_once.bat           Run the daily pipeline once
echo   schedule_create.bat    Create the daily 06:00 scheduled task
echo ====================================================
goto :eof

:err
echo.
echo [ERROR] Setup failed, see messages above.
exit /b 1
