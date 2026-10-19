# Full local quality gate (mirrors .github/workflows/ci.yml): rebuild containers, backend lint/types/tests/audit,
# frontend lint/types/build/audit, Playwright journeys, then scenario evidence.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$fail = @()
function Step($name, [scriptblock]$cmd) {
    Write-Host "==> $name" -ForegroundColor Cyan
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:fail += $name; Write-Host "FAILED: $name" -ForegroundColor Red }
}
Push-Location $root
try {
    Step "rebuild containers" { docker compose up -d --build --wait }
    Step "ruff" { docker compose exec -T api ruff check --no-cache app tests }
    Step "mypy" { docker compose exec -T api mypy --cache-dir=/tmp/mypy app }
    Step "pytest" { docker compose exec -T api python -m pytest -q -p no:warnings -p no:cacheprovider }
    Step "pip-audit" { docker compose exec -T api pip-audit -r requirements.txt }
    Push-Location apps\web
    Step "eslint" { npm run lint }
    Step "tsc" { npx tsc --noEmit }
    Step "next build" { npm run build }
    Step "npm audit (runtime deps)" { npm audit --omit=dev }
    Pop-Location
    Step "playwright e2e" { & (Join-Path $PSScriptRoot "e2e.ps1") }
    Step "scenario evidence" { & (Join-Path $PSScriptRoot "evidence.ps1") }
} finally { Pop-Location }
if ($fail.Count) { Write-Host "Failed steps: $($fail -join ', ')" -ForegroundColor Red; exit 1 }
Write-Host "All verification steps passed." -ForegroundColor Green
