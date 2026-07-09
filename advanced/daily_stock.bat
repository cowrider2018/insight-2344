@echo off
chcp 65001 >nul
set PYTHONUTF8=1
REM 進階工具位於 advanced\，切回專案根目錄執行
cd /d "%~dp0.."

if "%~1"=="" (
  echo Usage: advanced\daily_stock.bat ^<symbol^>
  echo   e.g. advanced\daily_stock.bat 2344
  exit /b 1
)
set STOCK_SYMBOL=%~1

echo ====================================================
echo  Step 2 排程分析: %STOCK_SYMBOL%
echo  (抓當日資料 -^> 產出決策卡)
echo ====================================================
".venv\Scripts\python.exe" src\build_dataset.py
".venv\Scripts\python.exe" src\daily_report.py
echo.
echo 決策卡 -^> reports\%STOCK_SYMBOL%\
