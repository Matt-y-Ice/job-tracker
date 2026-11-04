@echo off
rem Start the job tracker dashboard and open it in the browser.
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The project is not set up yet. Double-click setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m src.dashboard %*
if errorlevel 1 pause
