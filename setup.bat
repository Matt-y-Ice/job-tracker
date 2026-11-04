@echo off
rem One-time setup: Python virtual environment, packages, GPU check, Ollama models.
rem Run again any time to repair the install or download a newly chosen model.
setlocal
cd /d "%~dp0"

echo ============================================
echo   Job Tracker setup
echo ============================================
echo.

rem ---- 1. Find Python 3.10 or newer --------------------------------------
set "PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=py -3"
if defined PY goto :have_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PY=python"
if defined PY goto :have_python
echo [X] Python 3.10 or newer was not found.
echo     Download it from https://www.python.org/downloads/
echo     During install, tick "Add python.exe to PATH". Then run setup.bat again.
goto :fail

:have_python
for /f "delims=" %%v in ('%PY% -c "import sys; print(sys.version.split()[0])"') do set "PYVER=%%v"
echo [1/4] Python %PYVER% found.

rem ---- 2. Virtual environment --------------------------------------------
if exist ".venv\Scripts\python.exe" goto :have_venv
echo [2/4] Creating virtual environment in .venv ...
%PY% -m venv .venv
if errorlevel 1 (
    echo [X] Could not create the virtual environment.
    goto :fail
)
goto :venv_ready
:have_venv
echo [2/4] Virtual environment already exists.
:venv_ready
set "VPY=.venv\Scripts\python.exe"

rem ---- 3. Packages --------------------------------------------------------
echo [3/4] Installing Python packages ...
"%VPY%" -m pip install --upgrade pip --quiet --disable-pip-version-check
"%VPY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo [X] Package install failed. Check your internet connection and run setup.bat again.
    goto :fail
)
echo       Packages installed.

rem ---- 4. GPU and Ollama --------------------------------------------------
echo [4/4] Checking hardware and Ollama ...
echo.
"%VPY%" -m src.system_check gpu
echo.
"%VPY%" -m src.system_check ollama
if errorlevel 3 goto :need_ollama
if errorlevel 1 goto :fail

echo.
echo Setup finished. Put your resume in the "resume" folder, then double-click run.bat
goto :end

:need_ollama
echo.
echo Python setup is done. Install Ollama, then run setup.bat again to finish.
if /i not "%~1"=="--no-pause" pause
exit /b 0

:fail
echo.
echo Setup did not finish. Fix the problem above and run setup.bat again.
if /i not "%~1"=="--no-pause" pause
exit /b 1

:end
if /i not "%~1"=="--no-pause" pause
exit /b 0
