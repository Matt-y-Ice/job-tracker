@echo off
rem Reset this folder to a fresh download: deletes every personal file and everything the app
rem downloaded or created, so the project is safe to share (for example, push to GitHub).
rem It only touches files inside this folder. It cannot be undone.
rem
rem   scrub.bat                 asks before deleting
rem   scrub.bat --yes           no question (for scripts)
rem   scrub.bat --with-models   also remove the AI models Ollama downloaded for this app
setlocal EnableExtensions
cd /d "%~dp0"

set "ASSUME_YES="
set "WITH_MODELS="
for %%a in (%*) do (
    if /i "%%~a"=="--yes" set "ASSUME_YES=1"
    if /i "%%~a"=="--with-models" set "WITH_MODELS=1"
)

rem Refuse to run anywhere except this project's own folder.
if not exist "run.py" goto :wrong_folder
if not exist "src\pipeline.py" goto :wrong_folder

echo ============================================
echo   Job Tracker scrub: reset to a fresh copy
echo ============================================
echo.
echo This PERMANENTLY deletes, inside %CD% only:
echo   resume\                  your resume ^(the empty placeholder file stays^)
echo   config\preferences.json  your answers to the questions
echo   config\api_keys.json     optional API keys
echo   config\skill_catalog.json  your GitHub projects and skills
echo   data\                    job searches, rankings, the AI score cache, queued edits
echo   output\                  the Excel workbook, its backups, generated resumes and cover letters
echo   logs\                    run logs
echo   .venv\                   the Python environment ^(setup.bat rebuilds it^)
echo   __pycache__\             Python's compiled caches ^(they contain your Windows user path^)
if defined WITH_MODELS echo   Ollama models            the AI models listed in config\models.json
echo.
echo Nothing outside this folder is touched. Program files and default settings stay.
echo To keep your data, copy this whole folder somewhere else FIRST.
echo Close Excel, the dashboard window, and any running job search before continuing.
echo.
if defined ASSUME_YES goto :confirmed
set "ANSWER="
set /p "ANSWER=Type SCRUB to delete everything listed above, or press Enter to cancel: "
if not "%ANSWER%"=="SCRUB" goto :cancelled

:confirmed
echo.
set "PROBLEMS="

rem ---- personal files ------------------------------------------------------
for %%f in ("config\preferences.json" "config\api_keys.json" "config\skill_catalog.json") do (
    if exist %%f del /f /q %%f
)
for /f "delims=" %%f in ('dir /b /a-d "resume" 2^>nul') do (
    if /i not "%%f"==".gitkeep" del /f /q /a "resume\%%f"
)
for /f "delims=" %%d in ('dir /b /ad "resume" 2^>nul') do rmdir /s /q "resume\%%d"
if not exist "resume\.gitkeep" type nul > "resume\.gitkeep"
echo   Removed your resume and personal settings.

rem ---- generated data ------------------------------------------------------
call :remove_dir "data"
call :remove_dir "output"
call :remove_dir "logs"
echo   Removed job searches, rankings, the workbook, generated documents, and logs.

rem ---- downloaded AI models (only with --with-models) ----------------------
if defined WITH_MODELS call :remove_models

rem ---- environment and caches (last: nothing above needs them) -------------
call :remove_dir ".venv"
for /d /r "." %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
del /f /q /s "*.pyc" >nul 2>&1
echo   Removed the Python environment and compiled caches.

rem ---- check ---------------------------------------------------------------
echo.
for %%p in (data output logs .venv config\preferences.json config\api_keys.json config\skill_catalog.json) do (
    if exist "%%p" (
        echo   [!] Still here: %%p
        set "PROBLEMS=1"
    )
)
for /f "delims=" %%f in ('dir /b /a-d "resume" 2^>nul') do (
    if /i not "%%f"==".gitkeep" (
        echo   [!] Still here: resume\%%f
        set "PROBLEMS=1"
    )
)
if defined PROBLEMS goto :partial

echo Done. This folder is back to a fresh copy and is safe to share.
echo   To use it again:  double-click setup.bat
echo   To publish it:    git init, then git add . and git commit
echo   (.gitignore keeps personal files out of Git even after you use the app again.)
goto :end

:partial
echo Some items could not be deleted, usually because a program still has them open.
echo Close Excel and the dashboard window, then run scrub.bat again.
if not defined ASSUME_YES pause
exit /b 1

:remove_dir
if exist "%~1" rmdir /s /q "%~1"
exit /b 0

:remove_models
set "OLLAMA=ollama"
where ollama >nul 2>&1 || set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
if not exist "config\models.json" exit /b 0
for /f "delims=" %%m in ('powershell -NoProfile -Command "$m = Get-Content -Raw 'config\models.json' | ConvertFrom-Json; @($m.llm.model, $m.embedding.model, $m.documents.model) | Where-Object { $_ } | Sort-Object -Unique"') do (
    "%OLLAMA%" rm "%%m" >nul 2>&1 && echo   Removed Ollama model %%m
)
exit /b 0

:wrong_folder
echo scrub.bat must stay in the Job Tracker folder (next to run.py). Nothing was deleted.
if not defined ASSUME_YES pause
exit /b 1

:cancelled
echo Cancelled. Nothing was deleted.
exit /b 0

:end
if not defined ASSUME_YES pause
exit /b 0
