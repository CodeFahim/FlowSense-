# Creates .env with random local secrets if it does not exist. Never overwrites an existing .env.
# Usage (PowerShell, from the repository root):  .\scripts\setup.ps1
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root ".env"

function New-Secret([int]$bytes) {
    $b = New-Object byte[] $bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    $s = [Convert]::ToBase64String($b) -replace '[^A-Za-z0-9]', ''
    return $s.Substring(0, [Math]::Min(40, $s.Length))
}

if (Test-Path $envFile) {
    Write-Host ".env already exists - leaving it unchanged." -ForegroundColor Yellow
} else {
    $lines = Get-Content (Join-Path $root ".env.example")
    $lines = $lines -replace '^POSTGRES_PASSWORD=.*', "POSTGRES_PASSWORD=$(New-Secret 24)"
    $lines = $lines -replace '^LIFELINE_SANDBOX_EVENT_SECRET=.*', "LIFELINE_SANDBOX_EVENT_SECRET=$(New-Secret 48)"
    $lines = $lines -replace '^LIFELINE_DEMO_PASSWORD=.*', "LIFELINE_DEMO_PASSWORD=Demo-$(New-Secret 18)"
    $lines = $lines -replace '^LIFELINE_FRESH_ACCOUNT_PASSWORD=.*', "LIFELINE_FRESH_ACCOUNT_PASSWORD=Fresh-$(New-Secret 18)"
    Set-Content -Path $envFile -Value $lines -Encoding ascii
    Write-Host "Created .env with random local secrets." -ForegroundColor Green
}

docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker Desktop is not running. Start it from the Start menu, wait for 'Engine running', then re-run." -ForegroundColor Red
    exit 1
}
Write-Host "Docker is running. Next: .\scripts\start.ps1" -ForegroundColor Green
Write-Host "Demo sign-in password is the LIFELINE_DEMO_PASSWORD value in .env (or run .\scripts\demo-credentials.ps1)."
