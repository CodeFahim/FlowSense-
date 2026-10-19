# Runs the Playwright happy-path test against the running stack (.\scripts\start.ps1 first).
# One-time: cd apps\web; npm ci; npx playwright install chromium
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "FLOWSENSE_DEMO_PASSWORD=*" }
$env:FLOWSENSE_DEMO_PASSWORD = $line.Substring("FLOWSENSE_DEMO_PASSWORD=".Length)
Push-Location (Join-Path $root "apps\web")
try { npx playwright test; $code = $LASTEXITCODE } finally { Pop-Location; Remove-Item Env:\FLOWSENSE_DEMO_PASSWORD }
exit $code
