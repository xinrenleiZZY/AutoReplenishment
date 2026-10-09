# ============================================================
# Auto Replenishment System - DB restore (Phase 0 / G-01 companion, DANGEROUS)
#
# Restores a .dump into the target database. It runs pg_restore with
# --clean --if-exists, i.e. it DROPS and recreates objects first.
#
# Usage (interactive confirmation required):
#   powershell -ExecutionPolicy Bypass -File scripts\restore_db.ps1 ^
#       -DumpFile backups\auto_replenishment_20261008_150000.dump ^
#       -Database auto_replenishment
#
# Stop writers first (e.g. the API container) to avoid concurrent writes.
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$DumpFile,
    [string]$Container = "auto_replenish_db",
    [string]$DbUser    = "postgres",
    [Parameter(Mandatory = $true)][string]$Database
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $DumpFile)) {
    Write-Host "dump file not found: $DumpFile" -ForegroundColor Red
    exit 1
}

Write-Host "=== DB restore (DANGEROUS) ===" -ForegroundColor Yellow
Write-Host "dump   : $DumpFile"
Write-Host "container: $Container"
Write-Host "database : $Database"
Write-Host "This will DROP and recreate objects in the target database." -ForegroundColor Yellow
$confirm = Read-Host "type the target database name to confirm"
if ($confirm -ne $Database) {
    Write-Host "confirmation failed; aborted." -ForegroundColor Red
    exit 1
}

$inContainer = "/tmp/restore_$(Get-Date -Format 'yyyyMMdd_HHmmss').dump"
docker cp $DumpFile "${Container}:${inContainer}"
if ($LASTEXITCODE -ne 0) { Write-Host "docker cp failed" -ForegroundColor Red; exit 1 }

docker exec $Container pg_restore -U $DbUser -d $Database --clean --if-exists --no-owner --no-privileges $inContainer
$code = $LASTEXITCODE
docker exec $Container rm -f $inContainer | Out-Null

if ($code -ne 0) {
    Write-Host "pg_restore exit=$code (check dependency errors)" -ForegroundColor Yellow
    exit $code
}
Write-Host "restore done." -ForegroundColor Green
Write-Host "Next: restart API container, then check /health and /api/v1/ops/sync-freshness" -ForegroundColor Cyan
