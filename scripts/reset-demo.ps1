# Re-seeds the synthetic demo fixture (archives the old one; never deletes). Optional scenario name.
# Usage: .\scripts\reset-demo.ps1 [primary|partial_settlement|prompt_injection|beneficiary_change|reserve_breach|duplicate_payroll|customer_refusal|pending_recovery]
param([string]$Scenario = "primary")
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
Push-Location (Split-Path -Parent $PSScriptRoot)
try { docker compose exec -T api python -m app.seed $Scenario } finally { Pop-Location }
Write-Host "Signed-in users of the old fixture must sign in again."
