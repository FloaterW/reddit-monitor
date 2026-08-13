@echo off
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [%date% %time%] WATCHDOG ERROR: Python not found in PATH >> digest_run.log
    exit /b 1
)

echo [%date% %time%] Watchdog check... >> digest_run.log
python check_digest_ran.py >> digest_run.log 2>&1
set EXITCODE=%ERRORLEVEL%
echo. >> digest_run.log
exit /b %EXITCODE%
