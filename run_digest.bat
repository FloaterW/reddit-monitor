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

echo [%date% %time%] ERROR: Python not found. Create .venv or install Python 3. >> digest_run.log
exit /b 1

:python_found

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmm"') do set STAMP=%%I

if exist digest_run.log (
    for %%A in (digest_run.log) do if %%~zA GEQ 5242880 (
        if exist digest_run.previous.log del /q digest_run.previous.log
        move /y digest_run.log digest_run.previous.log >nul
    )
)

echo [%date% %time%] Starting digest run... >> digest_run.log
%PYTHON_CMD% daily_digest.py --monitor churning --save "digest_%STAMP%.md" --save-raw "digest_%STAMP%.json" --db data/monitor.db --status-file data/last_run_status.json --quality warn --quiet-summary >> digest_run.log 2>&1
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] Finished with exit code %EXITCODE% >> digest_run.log

if %EXITCODE% neq 0 (
    echo [%date% %time%] Sending failure alert... >> digest_run.log
    %PYTHON_CMD% notify_failure.py %EXITCODE% digest_run.log >> digest_run.log 2>&1
)

echo. >> digest_run.log
exit /b %EXITCODE%
