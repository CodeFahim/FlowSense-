# Optional local (non-Docker) Python environment for the API: venv + pinned dev dependencies.
# Needed for scripts\evidence.ps1 when running it from Windows. Requires Python 3.12 (py launcher).
$ErrorActionPreference = "Continue"
$api = Join-Path (Split-Path -Parent $PSScriptRoot) "services\api"
if (-not (Test-Path "$api\.venv")) { py -3.12 -m venv "$api\.venv"; if ($LASTEXITCODE -ne 0) { Write-Host "Python 3.12 not found (py -3.12)." -ForegroundColor Red; exit 1 } }
& "$api\.venv\Scripts\python.exe" -m pip install -q -r "$api\requirements-dev.txt"
if ($LASTEXITCODE -ne 0) { exit 1 }
Write-Host "API dev environment ready: $api\.venv" -ForegroundColor Green
