# Prints the synthetic demo accounts and the locally generated demo password from .env.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$line = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "LIFELINE_DEMO_PASSWORD=*" }
if (-not $line) { Write-Host "No .env found. Run .\scripts\setup.ps1 first." -ForegroundColor Red; exit 1 }
$pw = $line.Substring("LIFELINE_DEMO_PASSWORD=".Length)
Write-Host "Synthetic demo accounts (local only):"
Write-Host "  Owner : demo@lifeline.example"
Write-Host "  Staff : demo-staff@lifeline.example"
Write-Host "  Other tenant owner : owner@harbortea.example"
Write-Host "  Password (all three): $pw"
$fresh = Get-Content (Join-Path $root ".env") | Where-Object { $_ -like "LIFELINE_FRESH_ACCOUNT_PASSWORD=*" }
if ($fresh) {
    Write-Host ""
    Write-Host "Designated clean account (Start fresh, empty workspace):"
    Write-Host "  Email   : fresh@lifeline.example"
    Write-Host "  Password: $($fresh.Substring("LIFELINE_FRESH_ACCOUNT_PASSWORD=".Length))"
} else {
    Write-Host "LIFELINE_FRESH_ACCOUNT_PASSWORD is not set in .env, so the clean account is not created."
}
