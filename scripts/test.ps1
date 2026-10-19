# Runs the backend test suite inside the API container against the separate flowsense_test database.
# Native tools (docker, npm) write progress to stderr; rely on exit codes instead of Stop.
$ErrorActionPreference = "Continue"
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    docker compose exec -T api python -m pytest -q -p no:warnings -p no:cacheprovider @args
    $code = $LASTEXITCODE
} finally { Pop-Location }
exit $code
