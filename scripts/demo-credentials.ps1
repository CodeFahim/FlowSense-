# Prints the synthetic demo accounts and the locally generated demo password from .env.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "FLOWSENSE_DEMO_PASSWORD=*" }
if (-not $line) { Write-Host "No .env found. Run .\scripts\setup.ps1 first." -ForegroundColor Red; exit 1 }
$pw = $line.Substring("FLOWSENSE_DEMO_PASSWORD=".Length)
Write-Host "Synthetic demo accounts (local only):"
Write-Host "  Owner : demo@flowsense.example"
Write-Host "  Staff : demo-staff@flowsense.example"
Write-Host "  Other tenant owner : owner@harbortea.example"
Write-Host "  Password (all three): $pw"
$fresh = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "FLOWSENSE_FRESH_ACCOUNT_PASSWORD=*" }
if ($fresh) {
    Write-Host ""
    Write-Host "Designated clean account (Start fresh, empty workspace):"
    Write-Host "  Email   : fresh@flowsense.example"
    Write-Host "  Password: $($fresh.Substring("FLOWSENSE_FRESH_ACCOUNT_PASSWORD=".Length))"
} else {
    Write-Host "FLOWSENSE_FRESH_ACCOUNT_PASSWORD is not set in .env, so the clean account is not created."
}
