@echo off
cd /d "%~dp0"
if not exist "data\api-analysis" mkdir "data\api-analysis"
if not exist ".venv\Scripts\python.exe" exit /b 1
if exist "data\api-analysis\collection.log" (
    for %%A in ("data\api-analysis\collection.log") do if %%~zA GEQ 2097152 (
        if exist "data\api-analysis\collection.previous.log" del /q "data\api-analysis\collection.previous.log"
        move /y "data\api-analysis\collection.log" "data\api-analysis\collection.previous.log" >nul
    )
)
echo [%date% %time%] Collection only >> "data\api-analysis\collection.log"
".venv\Scripts\python.exe" -u collect_snapshot.py --monitor churning --store data/api-analysis/cache.db >> "data\api-analysis\collection.log" 2>&1
exit /b %ERRORLEVEL%
