# ============================================================
# 自动补货决策系统 - 一键启动
# 启动顺序: db -> api -> web（依赖关系）
# 使用: 右键"使用 PowerShell 运行" 或 在项目根目录执行
#   powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
# ============================================================
$ErrorActionPreference = "Stop"
Write-Host "=== 自动补货决策系统 启动 ===" -ForegroundColor Cyan

# 项目容器清单（注意: 不依赖 docker compose，直接按容器名管理）
$containers = @("auto_replenish_db", "auto_replenish_api", "auto_replenish_web")

# 1. 启动 db（数据库必须先就绪，api 才能连接）
Write-Host "[1/3] 启动数据库..." -ForegroundColor Yellow
docker start auto_replenish_db
if ($LASTEXITCODE -ne 0) { Write-Host "数据库启动失败！" -ForegroundColor Red; exit 1 }

# 等待 db 就绪（最多 30 秒）
$dbReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $check = docker exec auto_replenish_db pg_isready -U postgres -d auto_replenishment 2>$null
    if ($LASTEXITCODE -eq 0) { $dbReady = $true; break }
}
if (-not $dbReady) { Write-Host "数据库 30 秒内未就绪！" -ForegroundColor Red; exit 1 }
Write-Host "数据库已就绪 ✓" -ForegroundColor Green

# 2. 启动 api
Write-Host "[2/3] 启动 API 服务..." -ForegroundColor Yellow
docker start auto_replenish_api
if ($LASTEXITCODE -ne 0) { Write-Host "API 启动失败！" -ForegroundColor Red; exit 1 }

# 等待 api 健康（最多 90 秒，healthcheck 有 20s start_period + 30s interval）
$apiHealthy = $false
for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 1
    $status = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}running{{end}}" auto_replenish_api 2>$null
    if ($status -eq "healthy") { $apiHealthy = $true; break }
}
if (-not $apiHealthy) { Write-Host "API 未在 90 秒内变健康！" -ForegroundColor Red; exit 1 }
Write-Host "API 服务健康 ✓" -ForegroundColor Green

# 3. 启动 web
Write-Host "[3/3] 启动 Web 前端..." -ForegroundColor Yellow
docker start auto_replenish_web
if ($LASTEXITCODE -ne 0) { Write-Host "Web 启动失败！" -ForegroundColor Red; exit 1 }
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "=== 启动完成 ===" -ForegroundColor Green
Write-Host "  API:   http://localhost:9101/api/v1/calculation/results" -ForegroundColor White
Write-Host "  Web:   http://localhost:9100" -ForegroundColor White
Write-Host "  健康:  http://localhost:9101/health" -ForegroundColor White
Write-Host ""
docker ps --format "table {{.Names}}\t{{.Status}}" | Select-String -Pattern "auto_replenish"
