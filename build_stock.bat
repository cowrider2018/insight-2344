@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if "%~1"=="" (
  echo Usage: build_stock.bat ^<symbol^> [name]
  echo   e.g. build_stock.bat 2330 台積電
  exit /b 1
)
set STOCK_SYMBOL=%~1
if not "%~2"=="" set STOCK_NAME=%~2

echo ====================================================
echo  Step 1 策略建置: %STOCK_SYMBOL% %STOCK_NAME%
echo  (回補全部資料 -^> 校準 -^> 跑所有方法挑最佳 -^> strategy.json)
echo ====================================================
".venv\Scripts\python.exe" src\strategy_builder.py --full
echo.
echo 完成 -^> data\%STOCK_SYMBOL%\strategy.json
