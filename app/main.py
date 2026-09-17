"""FastAPI 应用入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import init_db, close_db, engine, async_session_factory
from app.tasks.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    await init_db()
    try:
        from app.services.operator_sync import sync_operators_from_products

        async with async_session_factory() as session:
            stats = await sync_operators_from_products(session)
        logger.info(f"启动时运营人员同步完成: {stats}")
    except Exception as e:
        logger.error(f"启动时运营人员同步失败: {e}")
    scheduler = setup_scheduler()
    scheduler.start()
    yield
    # 关闭时
    scheduler.shutdown()
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    description="AI驱动的跨境电商自动补货决策平台",
    version=settings.APP_VERSION,
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
        "app_info": "/api/v1/app-info",
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
    analysis,
    config,
    festival_calendar,
    category_leadtimes,
    sync_logs,
    sync_overview,
    operators,
    ai,
    product_costs,
    data_source,
    app_info,
)

app.include_router(products.router, prefix="/api/v1/products", tags=["产品管理"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["销量数据"])
app.include_router(calculation.router, prefix="/api/v1/calculation", tags=["计算任务"])
app.include_router(analysis.router, prefix="/api/v1/analysis", tags=["分析报告"])
app.include_router(config.router, prefix="/api/v1/config", tags=["自定义参数"])
app.include_router(festival_calendar.router, prefix="/api/v1/festival-calendar", tags=["节日日历"])
app.include_router(category_leadtimes.router, prefix="/api/v1/category-leadtimes", tags=["分类工期"])
app.include_router(sync_logs.router, prefix="/api/v1/sync-logs", tags=["同步日志"])
app.include_router(sync_overview.router, prefix="/api/v1/sync-overview", tags=["今日同步数据"])
app.include_router(operators.router, prefix="/api/v1/operators", tags=["运营人员管理"])
app.include_router(ai.router, prefix="/api/v1/ai", tags=["AI评估"])
app.include_router(product_costs.router, prefix="/api/v1/products", tags=["产品成本表"])
app.include_router(data_source.router, prefix="/api/v1/data-source", tags=["数据来源核验"])
app.include_router(app_info.router, prefix="/api/v1/app-info", tags=["应用信息"])
