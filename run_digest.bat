@echo off
cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [%date% %time%] ERROR: Python not found in PATH >> digest_run.log
    exit /b 1
)

for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /value') do set "DT=%%I"
set STAMP=%DT:~0,8%_%DT:~8,4%

echo [%date% %time%] Starting digest run... >> digest_run.log
python daily_digest.py --posts 10 --time day --save "digest_%STAMP%.md" --save-raw "digest_%STAMP%.json" >> digest_run.log 2>&1
set EXITCODE=%ERRORLEVEL%
echo [%date% %time%] Finished with exit code %EXITCODE% >> digest_run.log
echo. >> digest_run.log
exit /b %EXITCODE%
