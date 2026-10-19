# Runs all Tier 1 scenarios against the running stack and writes evidence/ (JSONL + summary).
# Requires: .\scripts\start.ps1 already running, and the API venv (.\scripts\dev-setup.ps1) or any Python with httpx.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "FLOWSENSE_DEMO_PASSWORD=*" }
$env:FLOWSENSE_DEMO_PASSWORD = $line.Substring("FLOWSENSE_DEMO_PASSWORD=".Length)
$py = Join-Path $root "services\api\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "py" }
& $py (Join-Path $root "scripts\run_scenarios.py")
$code = $LASTEXITCODE
Remove-Item Env:\FLOWSENSE_DEMO_PASSWORD
Write-Host "Evidence written to $root\evidence (summary.md, operations.jsonl)."
exit $code
