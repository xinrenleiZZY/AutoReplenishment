# ============================================================
# 自动补货决策系统 - 一键停止
# 停止顺序: web -> api -> db
# 使用: 右键"使用 PowerShell 运行" 或 在项目根目录执行
#   powershell -ExecutionPolicy Bypass -File scripts\stop_all.ps1
# ============================================================
$ErrorActionPreference = "Stop"
Write-Host "=== 自动补货决策系统 停止 ===" -ForegroundColor Cyan

Write-Host "[1/3] 停止 Web 前端..." -ForegroundColor Yellow
docker stop auto_replenish_web

Write-Host "[2/3] 停止 API 服务..." -ForegroundColor Yellow
docker stop auto_replenish_api

Write-Host "[3/3] 停止数据库..." -ForegroundColor Yellow
docker stop auto_replenish_db

Write-Host ""
Write-Host "=== 已全部停止 ===" -ForegroundColor Green
