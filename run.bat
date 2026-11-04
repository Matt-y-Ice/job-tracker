@echo off
rem Double-click to find, rank, and update your jobs. Options (e.g. --skip-scrape) can be added
rem by running it from a command window:  run.bat --skip-scrape
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo The project is not set up yet. Double-click setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run.py %*
echo.
pause
