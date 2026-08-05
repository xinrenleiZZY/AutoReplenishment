"""FastAPI 应用入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import init_db, close_db, engine
from app.tasks.scheduler import setup_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    await init_db()
    scheduler = setup_scheduler()
    scheduler.start()
    yield
    # 关闭时
    scheduler.shutdown()
    await close_db()


app = FastAPI(
    title="自动补货决策系统",
    description="AI驱动的跨境电商自动补货决策平台",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 配置：默认 * 不带凭证；生产环境建议通过 CORS_ORIGINS 配置白名单
_cors_origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()] or ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """服务信息"""
    return {
        "name": app.title,
        "version": app.version,
        "env": settings.APP_ENV,
        "docs": "/docs",
        "health": "/health",
    }


async def _health_result():
    """健康检查结果（含数据库连通性，DB 不可用时返回 503）"""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"

    if db_status != "ok":
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "env": settings.APP_ENV, "database": db_status},
        )
    return {"status": "ok", "env": settings.APP_ENV, "database": db_status}


@app.get("/health")
async def health_check():
    """健康检查"""
    return await _health_result()


@app.get("/api/health")
async def api_health_check():
    """健康检查别名（供前端经 /api 代理访问）"""
    return await _health_result()


# 注册路由
from app.api.v1 import (  # noqa: E402
    products,
    sales,
    calculation,
    festival_calendar,
    category_leadtimes,
    seasonal_curves,
    sync_logs,
)

app.include_router(products.router, prefix="/api/v1/products", tags=["产品管理"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["销量数据"])
app.include_router(calculation.router, prefix="/api/v1/calculation", tags=["计算任务"])
app.include_router(festival_calendar.router, prefix="/api/v1/festival-calendar", tags=["节日日历"])
app.include_router(category_leadtimes.router, prefix="/api/v1/category-leadtimes", tags=["分类工期"])
app.include_router(seasonal_curves.router, prefix="/api/v1/seasonal-curves", tags=["季节曲线"])
app.include_router(sync_logs.router, prefix="/api/v1/sync-logs", tags=["同步日志"])
