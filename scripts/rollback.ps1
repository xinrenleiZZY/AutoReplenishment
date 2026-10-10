# Phase 1 / G-10：按镜像标签回滚（默认回到上一次发布前的标签）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\rollback.ps1                 # 回到 _tmp\last_release.json 记录的上一版
#   powershell -ExecutionPolicy Bypass -File scripts\rollback.ps1 -Tag 538222a    # 回到指定 commit 标签
#
# 说明：只切换**镜像与容器**，不回滚数据库；若本次发布含迁移，请另按方案第 7 章
#      执行反向迁移（alembic downgrade <revision>）。

param(
    [string]$Tag
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $Tag) {
    $rec = Get-Content "$root\_tmp\last_release.json" -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    if (-not $rec) { throw "未找到 _tmp\last_release.json，请用 -Tag 指定要回滚到的镜像标签" }
    $Tag = $rec.previous_api
    if (-not $Tag) { throw "发布记录里没有可用的上一版标签，请用 -Tag 指定" }
}

Write-Host "== 回滚到镜像标签 $Tag ==" -ForegroundColor Cyan
docker image inspect "auto_replenish_api:$Tag" *> $null
if ($LASTEXITCODE -ne 0) { throw "本地不存在镜像 auto_replenish_api:$Tag" }

docker tag "auto_replenish_api:$Tag" yy021--api:latest
docker image inspect "auto_replenish_web:$Tag" *> $null
if ($LASTEXITCODE -eq 0) { docker tag "auto_replenish_web:$Tag" yy021--web:latest } else { Write-Warning "无 auto_replenish_web:$Tag，仅回滚 api" }

docker compose up -d --force-recreate api web
if ($LASTEXITCODE -ne 0) { throw "容器回滚失败" }

Start-Sleep -Seconds 12
$h = Invoke-RestMethod -Uri "http://127.0.0.1:9101/health" -TimeoutSec 10
Write-Host "回滚后 /health => $($h.status)/$($h.database)" -ForegroundColor Green
