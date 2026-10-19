# Stops the containers. Keeps the database volume (your data) - this script never deletes volumes.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
Push-Location (Split-Path -Parent $PSScriptRoot)
try { docker compose stop } finally { Pop-Location }
Write-Host "Stopped. Data kept. Start again with .\scripts\start.ps1"
