"""FastAPI 应用入口"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import settings
from app.database import init_db, close_db, engine, async_session_factory
from app.tasks.scheduler import setup_scheduler, set_scheduler

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    """统一日志格式（Phase 1 / G-11 基础）：时间 + 级别 + 模块 + 消息，输出到 stdout。

    仅在 root 还没有 StreamHandler 时添加，避免与 uvicorn 自带配置重复打印。
    """
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    root = logging.getLogger()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
        ))
        root.addHandler(handler)
    root.setLevel(level)


_setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时
    await init_db()
    try:
        from app.services.config_service import sync_settings_overrides

        async with async_session_factory() as session:
            applied = await sync_settings_overrides(session)
        logger.info(f"启动时自定义参数同步到 settings 完成: {applied}")
    except Exception as e:
        logger.error(f"启动时自定义参数同步失败: {e}")
    try:
        from app.services.operator_sync import sync_operators_from_products

        async with async_session_factory() as session:
            stats = await sync_operators_from_products(session)
        logger.info(f"启动时运营人员同步完成: {stats}")
    except Exception as e:
        logger.error(f"启动时运营人员同步失败: {e}")
    try:
        from app.services.festival_year_roll import ensure_festival_year_current

        async with async_session_factory() as session:
            stats = await ensure_festival_year_current(session)
        logger.info(f"启动时节日日历年校验完成: {stats}")
    except Exception as e:
        logger.error(f"启动时节日日历年校验失败: {e}")
    try:
        # Phase 0 / G-14：把"进程被杀导致没有终态"的历史记录标记为 interrupted
        from app.tasks.scheduler import mark_stale_running_interrupted

        n = await mark_stale_running_interrupted(6)
        logger.info(f"启动巡检：标记 {n} 条超时 running 同步记录为 interrupted")
    except Exception as e:
        logger.error(f"启动巡检（超时 running 记录）失败: {e}")
    # Phase 3 / B-03：调度器归属可切换——外置模式下 API 不再启动 APScheduler
    scheduler = None
    if settings.RUN_SCHEDULER_IN_API:
        scheduler = setup_scheduler()
        set_scheduler(scheduler)
        scheduler.start()
        logger.info("进程内调度器已启动（RUN_SCHEDULER_IN_API=true）")
    else:
        logger.info("进程内调度器未启动（RUN_SCHEDULER_IN_API=false），定时任务由独立 scheduler 服务负责")
    # Phase 3 / B-04：后台任务持久化巡检 + 恢复未到期的定时任务
    try:
        from app.services import job_store

        # 单 worker 部署：进程一重启，内存里的执行协程即消失 → 遗留 running 行立即判为中断。
        # B-03：任务大厅单ASIN任务由调度服务执行，这里不碰（否则会误标别人的在跑任务）。
        n = await job_store.mark_orphan_running_interrupted(exclude_kinds=("task-hall-single",))
        logger.info(f"启动巡检：标记 {n} 条遗留 running 后台任务为 interrupted")
        n2 = await job_store.mark_stale_running_interrupted(6)
        logger.info(f"启动巡检：标记 {n2} 条超时后台任务为 interrupted")
    except Exception as e:
        logger.error(f"启动巡检（后台任务）失败: {e}")
    try:
        from datetime import datetime

        from apscheduler.triggers.date import DateTrigger

        from app.api.v1.calculation import _run_single_notify_task, _single_tasks
        from app.services.job_store import pending_scheduled_singles

        for p in (await pending_scheduled_singles()) if scheduler is not None else []:
            if not p.get("asin") or not p.get("run_at"):
                continue
            run_dt = datetime.fromisoformat(str(p["run_at"]))
            if run_dt <= datetime.now():
                continue
            task = {
                "id": p["task_id"], "asin": p["asin"], "operator": p.get("operator"),
                "run_at": p["run_at"], "status": "pending",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "started_at": None, "finished_at": None, "stats": None, "error": None,
            }
            _single_tasks[p["task_id"]] = task
            scheduler.add_job(
                _run_single_notify_task, trigger=DateTrigger(run_date=run_dt), args=[task],
                id=f"task_hall_single_{p['task_id']}", name=f"单ASIN私发 {p['asin']}",
                replace_existing=True,
            )
            logger.info(f"启动恢复定时任务：{p['asin']} @ {run_dt:%Y-%m-%d %H:%M}")
    except Exception as e:
        logger.error(f"启动恢复定时任务失败: {e}")
    yield
    # 关闭时
    if scheduler is not None:
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

# ── 写操作鉴权（Phase 0 / G-04）：API_AUTH_TOKEN 为空时不生效 ──
_AUTH_EXEMPT_PATHS = {"/api/v1/app-info"}


@app.middleware("http")
async def _api_token_guard(request: Request, call_next):
    token = (settings.API_AUTH_TOKEN or "").strip()
    path = request.url.path
    if token and path.startswith("/api/v1") and path not in _AUTH_EXEMPT_PATHS:
        supplied = (request.headers.get("x-api-token") or "").strip()
        if supplied != token:
            return JSONResponse(
                status_code=401,
                content={"detail": "未授权：缺少或错误的 X-API-Token（Phase 0 G-04 写操作鉴权）"},
            )
    return await call_next(request)


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
    semantic_classifications,
    ops,
    purchase_orders,
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
app.include_router(semantic_classifications.router, prefix="/api/v1/semantic-classifications", tags=["缓存天数语义分类"])
app.include_router(ops.router, prefix="/api/v1/ops", tags=["运维观测"])
app.include_router(purchase_orders.router, prefix="/api/v1/purchase-orders", tags=["采购单产品明细"])
