# Checks API (with database) and web health.
$ok = $true
try { $h = Invoke-RestMethod -Uri "http://localhost:8000/api/health" -TimeoutSec 5; Write-Host "API: $($h.status), database: $($h.database), AI mode: $($h.ai_mode)" -ForegroundColor Green }
catch { Write-Host "API not healthy: $($_.Exception.Message)" -ForegroundColor Red; $ok = $false }
try { $r = Invoke-WebRequest -Uri "http://localhost:3000/login" -UseBasicParsing -TimeoutSec 5; Write-Host "Web: HTTP $($r.StatusCode)" -ForegroundColor Green }
catch { Write-Host "Web not healthy: $($_.Exception.Message)" -ForegroundColor Red; $ok = $false }
if (-not $ok) { exit 1 }
