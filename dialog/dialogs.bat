@echo off
rem Bitrix24 chats analysis: input\*.csv -> dashboard.html. Local only: no internet, no AI.
cd /d "%~dp0"
..\.venv\Scripts\python.exe dialog_processor.py --rebuild
if errorlevel 1 (
    echo ERROR: see messages above.
    pause
    exit /b 1
)
start "" "dashboard.html"
pause
