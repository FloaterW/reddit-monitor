@echo off
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PYTHON_CMD=.venv\Scripts\python.exe"
    goto :python_found
)

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3"
    goto :python_found
)

where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=python"
    goto :python_found
)

echo [%date% %time%] WATCHDOG ERROR: Python not found. Create .venv or install Python 3. >> digest_run.log
exit /b 1

:python_found

echo [%date% %time%] Watchdog check... >> digest_run.log
%PYTHON_CMD% check_digest_ran.py --status-file data/last_run_status.json >> digest_run.log 2>&1
set EXITCODE=%ERRORLEVEL%
echo. >> digest_run.log
exit /b %EXITCODE%
