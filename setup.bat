@echo off
chcp 65001 >nul
set PYTHONUTF8=1
setlocal
cd /d "%~dp0"

echo ====================================================
echo  2344 Winbond pre-market analysis - environment setup
echo ====================================================
echo.

echo [1/5] Install Python packages (requirements.txt)
python -m pip install -r requirements.txt
if errorlevel 1 goto :err

echo.
echo [2/5] Install Playwright Chromium (for news scraping)
python -m playwright install chromium
if errorlevel 1 goto :err

echo.
echo [3/5] Initialize timeline database data\market.db
python src\timeline_db.py --init
if errorlevel 1 goto :err

echo.
echo [4/5] Backfill news/chips/revenue/candles from existing snapshots
python src\ingest.py --backfill-json

echo.
echo [5/5] Backfill daily candles (Fugle) and overnight US (Micron/SOX, Yahoo)
python src\ingest.py --backfill-candles
python src\ingest.py --backfill-us

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
