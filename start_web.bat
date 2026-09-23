@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=python"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"
if not defined APP_MODE set "APP_MODE=live"
if not defined PORT set "PORT=5001"
if "%APP_MODE%"=="demo" goto demo
if not defined QWEN_API_KEY if not defined DASHSCOPE_API_KEY (
  echo Set QWEN_API_KEY or use APP_MODE=demo. DASHSCOPE_API_KEY is still supported.
  exit /b 1
)
if not defined FLIGHT_DB_PATH set "FLIGHT_DB_PATH=data\flight_weather.db"
if not exist "%FLIGHT_DB_PATH%" "%PYTHON%" data\init_flight_weather_db.py --db "%FLIGHT_DB_PATH%"
if errorlevel 1 exit /b 1
goto serve
:demo
if not defined FLIGHT_DB_PATH set "FLIGHT_DB_PATH=data\demo_operations.db"
if not exist "%FLIGHT_DB_PATH%" "%PYTHON%" data\generate_demo_data.py --db "%FLIGHT_DB_PATH%"
if errorlevel 1 exit /b 1
:serve
echo http://127.0.0.1:%PORT%
"%PYTHON%" app.py
