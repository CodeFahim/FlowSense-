# Builds and starts db + api + web in Docker. Data persists in the 'lifeline_pgdata' volume.
# Usage: .\scripts\start.ps1    Then open http://localhost:3000
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $root ".env"))) { Write-Host "No .env yet - running setup first." -ForegroundColor Yellow; & (Join-Path $PSScriptRoot "setup.ps1") }
docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop is not running. Start it and wait for 'Engine running'." -ForegroundColor Red; exit 1 }
Push-Location $root
try {
    docker compose up -d --build --wait
    if ($LASTEXITCODE -ne 0) { Write-Host "Startup failed. See: docker compose logs api" -ForegroundColor Red; exit 1 }
} finally { Pop-Location }
& (Join-Path $PSScriptRoot "health.ps1")
Write-Host "FlowSense is running: http://localhost:3000  (API docs: http://localhost:8000/api/docs)" -ForegroundColor Green
Write-Host "Sign-in details: .\scripts\demo-credentials.ps1"
