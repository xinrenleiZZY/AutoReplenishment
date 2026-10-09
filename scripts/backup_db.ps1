# ============================================================
# Auto Replenishment System - DB backup (Phase 0 / G-01)
#
# Strategy: run pg_dump -Fc INSIDE the db container (custom format is
# already compressed), then docker cp the file out. This avoids piping
# binary data through PowerShell (which can corrupt it).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\backup_db.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\backup_db.ps1 -KeepDays 30
# ============================================================
param(
    [string]$Container = "auto_replenish_db",
    [string]$DbUser    = "postgres",
    [string]$Database  = "auto_replenishment",
    [string]$BackupDir = "",
    [int]$KeepDays     = 30
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $BackupDir) { $BackupDir = Join-Path $repoRoot "backups" }
if (-not (Test-Path -LiteralPath $BackupDir)) {
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
}

$stamp       = Get-Date -Format "yyyyMMdd_HHmmss"
$fileName    = "auto_replenishment_${stamp}.dump"
$localPath   = Join-Path $BackupDir $fileName
$inContainer = "/tmp/$fileName"

Write-Host "=== DB backup ===" -ForegroundColor Cyan
Write-Host "container=$Container db=$Database target=$localPath"

# 1) container must be running
$running = (docker inspect --format "{{.State.Running}}" $Container 2>$null)
if ($running -ne "true") {
    Write-Host "container $Container is not running; abort." -ForegroundColor Red
    exit 1
}

# 2) pg_dump inside container (custom format = compressed + selective restore)
docker exec $Container pg_dump -U $DbUser -d $Database --no-owner --no-privileges -Fc -f $inContainer
if ($LASTEXITCODE -ne 0) {
    Write-Host "pg_dump failed (exit=$LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# 3) copy out, then clean temp file in container
docker cp "${Container}:${inContainer}" $localPath
if ($LASTEXITCODE -ne 0) {
    Write-Host "docker cp failed (exit=$LASTEXITCODE)" -ForegroundColor Red
    docker exec $Container rm -f $inContainer | Out-Null
    exit 1
}
docker exec $Container rm -f $inContainer | Out-Null

$sizeMb = [Math]::Round((Get-Item -LiteralPath $localPath).Length / 1MB, 2)
Write-Host "backup done: $fileName ($sizeMb MB)" -ForegroundColor Green

# 4) retention: remove files older than KeepDays
$cutoff = (Get-Date).AddDays(-$KeepDays)
$old = Get-ChildItem -LiteralPath $BackupDir -Filter "auto_replenishment_*.dump" |
       Where-Object { $_.LastWriteTime -lt $cutoff }
foreach ($f in $old) {
    Remove-Item -LiteralPath $f.FullName -Force
    Write-Host "removed expired backup: $($f.Name)"
}

$all = @(Get-ChildItem -LiteralPath $BackupDir -Filter "auto_replenishment_*.dump" |
         Sort-Object LastWriteTime -Descending)
Write-Host ("total backups: {0} (keep {1} days)" -f $all.Count, $KeepDays)
Write-Host "latest 5:" -ForegroundColor Cyan
$all | Select-Object -First 5 | ForEach-Object {
    Write-Host ("  {0}  {1} MB" -f $_.Name, [Math]::Round($_.Length / 1MB, 2))
}
