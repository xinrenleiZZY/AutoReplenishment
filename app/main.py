"""FastAPI 应用入口"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, close_db
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

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "env": settings.APP_ENV}


# 注册路由
from app.api.v1 import products, sales, calculation  # noqa: E402

app.include_router(products.router, prefix="/api/v1/products", tags=["产品管理"])
app.include_router(sales.router, prefix="/api/v1/sales", tags=["销量数据"])
app.include_router(calculation.router, prefix="/api/v1/calculation", tags=["计算任务"])
