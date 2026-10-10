# Phase 1 / G-10：按 commit SHA 打标的发布脚本（构建 → 打标 → 切换 → 冒烟）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1                # 发布当前 HEAD
#   powershell -ExecutionPolicy Bypass -File scripts\release.ps1 -SkipSmoke     # 跳过冒烟
#
# 说明：镜像同时打两个标签——<short-sha>（可回滚的确定版本）与 latest（compose 使用）。
#      上一次发布的标签记录在 _tmp\last_release.json，供 rollback.ps1 使用。

param(
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$sha = (git rev-parse --short HEAD).Trim()
$dirty = (git status --porcelain 2>$null | Where-Object { $_ -notlike '??*' } | Measure-Object).Count
if ($dirty -gt 0) { Write-Warning "工作区有 $dirty 个未提交改动，发布内容将包含它们（建议先提交）" }

Write-Host "== 发布 commit $sha ==" -ForegroundColor Cyan

# 记住上一次的 api 标签，便于回滚
$prev = (docker images auto_replenish_api --format "{{.Tag}}" | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
$prevWeb = (docker images auto_replenish_web --format "{{.Tag}}" | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
$prevSched = (docker images auto_replenish_scheduler --format "{{.Tag}}" | Where-Object { $_ -ne "latest" } | Select-Object -First 1)

Write-Host "1/4 构建镜像..." -ForegroundColor Yellow
$env:GIT_SHA = $sha   # 烧进镜像，供冒烟时核对"容器跑的版本 = 本次 commit"
docker compose build api web scheduler
if ($LASTEXITCODE -ne 0) { throw "构建失败，已中止（未切换容器）" }

Write-Host "2/4 打标 $sha ..." -ForegroundColor Yellow
docker tag yy021--api "auto_replenish_api:$sha"
docker tag yy021--web "auto_replenish_web:$sha"
docker tag yy021--scheduler "auto_replenish_scheduler:$sha"
if ($LASTEXITCODE -ne 0) { throw "打标失败" }

Write-Host "3/4 切换容器..." -ForegroundColor Yellow
docker compose up -d api web scheduler
if ($LASTEXITCODE -ne 0) { throw "容器切换失败" }

if (-not $SkipSmoke) {
    Write-Host "4/4 冒烟测试..." -ForegroundColor Yellow
    Start-Sleep -Seconds 12
    $ok = $true
    try {
        $h = Invoke-RestMethod -Uri "http://127.0.0.1:9101/health" -TimeoutSec 10
        $ok = $ok -and ($h.status -eq "ok")
        Write-Host "  /health => $($h.status)/$($h.database)"
    } catch { $ok = $false; Write-Warning "  /health 失败: $_" }
    try {
        # 走前端代理（验证 web 中间件注入了 X-API-Token）
        $s = Invoke-RestMethod -Uri "http://127.0.0.1:9100/api/v1/calculation/run-summary" -TimeoutSec 20
        Write-Host "  web 代理 run-summary => 到期 $($s.due) / 实际计算 $($s.computed)"
    } catch { $ok = $false; Write-Warning "  web 代理失败: $_" }
    try {
        $st = docker inspect --format "{{.State.Health.Status}}" auto_replenish_api
        $stw = docker inspect --format "{{.State.Health.Status}}" auto_replenish_web
        Write-Host "  healthcheck => api:$st web:$stw"
        $ok = $ok -and ($st -ne "unhealthy") -and ($stw -ne "unhealthy")
    } catch { Write-Warning "  healthcheck 读取失败: $_" }
    try {
        # 核对镜像内烧入的 commit SHA，防止"构建了但容器仍是旧镜像"
        $inner = (docker exec auto_replenish_api printenv APP_BUILD_SHA).Trim()
        Write-Host "  容器内 APP_BUILD_SHA => $inner（本次 $sha）"
        if ($inner -ne $sha) { $ok = $false; Write-Warning "  容器内版本与本次发布不一致，请用 --force-recreate 重新切换" }
        $innerSched = (docker exec auto_replenish_scheduler printenv APP_BUILD_SHA).Trim()
        Write-Host "  scheduler 容器 APP_BUILD_SHA => $innerSched（本次 $sha）"
        if ($innerSched -ne $sha) { $ok = $false; Write-Warning "  scheduler 容器版本与本次发布不一致（注意：scheduler 是独立服务，需单独 build）" }
    } catch { Write-Warning "  APP_BUILD_SHA 读取失败: $_" }
    Remove-Item Env:\GIT_SHA -ErrorAction SilentlyContinue
    if (-not $ok) {
        Write-Warning "冒烟未全绿：可执行 scripts\rollback.ps1 回滚到上一个标签"
    } else {
        Write-Host "冒烟通过 ✔" -ForegroundColor Green
    }
}

$record = [pscustomobject]@{
    released_at   = (Get-Date).ToString("s")
    commit        = $sha
    image_tag     = $sha
    previous_api  = $prev
    previous_web  = $prevWeb
    previous_scheduler = $prevSched
}
$record | ConvertTo-Json | Out-File -Encoding utf8 "$root\_tmp\last_release.json"
Write-Host "发布记录：_tmp\last_release.json（如需回滚：scripts\rollback.ps1）" -ForegroundColor Cyan
