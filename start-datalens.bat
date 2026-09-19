@echo off
chcp 65001 >nul
echo [DataLens] Starting all services in company-ai stack (HC=1, UI_PORT=8088)...
set HC=1
set UI_PORT=8088

docker compose up -d --remove-orphans
if errorlevel 1 (
    echo [WARNING] Reconnecting containers to updated network...
    docker compose down
    docker compose up -d
)

echo.
echo [DataLens] All containers are running!
echo   - AIKOS Web Server : http://localhost:8080
echo   - Yandex DataLens  : http://localhost:8088
echo   - n8n Automation   : http://localhost:5678
