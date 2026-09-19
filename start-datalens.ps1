# Start company-ai stack including DataLens

Write-Host "[DataLens] Starting all services in company-ai stack (HC=1, UI_PORT=8088)..." -ForegroundColor Cyan
$env:HC = "1"
$env:UI_PORT = "8088"

docker compose up -d

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "[DataLens] All containers are running!" -ForegroundColor Green
    Write-Host "  - AIKOS Web Server : http://localhost:8080" -ForegroundColor Cyan
    Write-Host "  - Yandex DataLens  : http://localhost:8088" -ForegroundColor Cyan
    Write-Host "  - n8n Automation   : http://localhost:5678" -ForegroundColor Cyan
    Write-Host "  - PostgreSQL       : localhost:5433" -ForegroundColor Cyan
} else {
    Write-Error "Failed to start services. Please check Docker Desktop status."
}
