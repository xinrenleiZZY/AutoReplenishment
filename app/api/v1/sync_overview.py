"""今日同步数据聚合概览 API 路由"""

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.historical_monthly import HistoricalMonthlyStats
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.models.sales import SalesData
from app.models.sync_log import SyncLog
from app.schemas.reference import SyncOverviewResponse, SyncSourceStat, SyncTaskStat

router = APIRouter()

# 同步类型 -> 展示名（与 sync_logs.SYNC_RUNNERS 保持一致）
SYNC_TYPE_LABELS = {
    "product": "产品同步",
    "sales": "销量同步",
    "inventory": "库存同步",
    "box_quantity": "箱规同步",
    "purchase_orders": "采购待到货",
    "sales_statistics": "销售统计",
    "fx_rate": "汇率",
    "profit": "利润回填",
    "acos": "ACOS回填",
    "monthly_lingxing": "领星月度回填",
    "daily_sales": "逐日销量",
    "profit_report": "经营利润报表",
    "purchase_sources": "采购计划/采购单",
    "base_analysis": "基础数据分析",
}


@router.get("", response_model=SyncOverviewResponse)
async def sync_overview(session: AsyncSession = Depends(get_session)):
    """今日同步数据聚合概览：同步任务执行情况 + 各数据源最新内容"""
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    tomorrow_start = today_start + timedelta(days=1)

    # ---- 1. 今日同步任务执行情况（按类型聚合，取最新一次）----
    logs = (await session.execute(
        select(SyncLog).where(SyncLog.started_at >= today_start).order_by(SyncLog.started_at.desc())
    )).scalars().all()

    latest_by_type: dict[str, SyncLog] = {}
    for log in logs:
        if log.sync_type not in latest_by_type:
            latest_by_type[log.sync_type] = log

    tasks: list[SyncTaskStat] = []
    for sync_type in SYNC_TYPE_LABELS:
        log = latest_by_type.get(sync_type)
        if log is None:
            tasks.append(SyncTaskStat(sync_type=sync_type, label=SYNC_TYPE_LABELS[sync_type], status=None))
        else:
            tasks.append(SyncTaskStat(
                sync_type=sync_type,
                label=SYNC_TYPE_LABELS[sync_type],
                status=log.status,
                total_count=log.total_count,
                success_count=log.success_count,
                error_message=log.error_message,
                started_at=log.started_at,
                completed_at=log.completed_at,
            ))

    # ---- 2. 各数据源今日内容聚合 ----
    sources: list[SyncSourceStat] = []

    # 产品档案
    products_total = (await session.execute(select(func.count()).select_from(Product))).scalar() or 0
    products_updated_today = (await session.execute(
        select(func.count()).select_from(Product).where(Product.updated_at >= today_start, Product.updated_at < tomorrow_start)
    )).scalar() or 0
    sources.append(SyncSourceStat(
        key="products", label="产品档案", count=products_updated_today,
        status="ok" if products_updated_today else "warn",
        detail=f"共 {products_total} 个在售产品",
    ))

    # 销量明细（sales_data：以最新日期为准）
    sales_latest = (await session.execute(select(func.max(SalesData.date)))).scalar()
    if sales_latest is None:
        sources.append(SyncSourceStat(key="sales_data", label="销量明细", count=0, status="none", detail="无数据"))
    else:
        sales_count = (await session.execute(
            select(func.count()).select_from(SalesData).where(SalesData.date == sales_latest)
        )).scalar() or 0
        fresh = sales_latest >= today - timedelta(days=2)
        sources.append(SyncSourceStat(
            key="sales_data", label="销量明细", count=sales_count,
            status="ok" if fresh else "warn",
            detail=f"最新日期 {sales_latest.isoformat()}",
        ))

    # 领星销售快照（今日）
    snapshot_today = (await session.execute(
        select(func.count()).select_from(DailySalesSnapshot).where(DailySalesSnapshot.snapshot_date == today)
    )).scalar() or 0
    snapshot_latest = (await session.execute(select(func.max(DailySalesSnapshot.snapshot_date)))).scalar()
    sources.append(SyncSourceStat(
        key="daily_snapshot", label="领星销售快照", count=snapshot_today,
        status="ok" if snapshot_today else "warn",
        detail=f"最新快照 {snapshot_latest.isoformat() if snapshot_latest else '无'}",
    ))

    # 库存快照（今日）
    inventory_today = (await session.execute(
        select(func.count()).select_from(InventorySnapshot).where(InventorySnapshot.snapshot_date == today)
    )).scalar() or 0
    inventory_latest = (await session.execute(select(func.max(InventorySnapshot.snapshot_date)))).scalar()
    sources.append(SyncSourceStat(
        key="inventory_snapshot", label="库存快照", count=inventory_today,
        status="ok" if inventory_today else "warn",
        detail=f"最新快照 {inventory_latest.isoformat() if inventory_latest else '无'}",
    ))

    # 月度历史 - 领星（今日更新）
    lingxing_today = (await session.execute(
        select(func.count()).select_from(HistoricalMonthlyStats)
        .where(HistoricalMonthlyStats.source == "lingxing", HistoricalMonthlyStats.updated_at >= today_start,
               HistoricalMonthlyStats.updated_at < tomorrow_start)
    )).scalar() or 0
    lingxing_cover = (await session.execute(
        select(func.count()).select_from(HistoricalMonthlyStats)
        .where(HistoricalMonthlyStats.source == "lingxing")
    )).scalar() or 0
    sources.append(SyncSourceStat(
        key="monthly_lingxing", label="领星月度历史", count=lingxing_today,
        status="ok" if lingxing_today else "none",
        detail=f"累计覆盖 {lingxing_cover} 条",
    ))

    # 月度历史 - SIF 已停用（不再回填，历史数据仅作展示，不参与决策）

    # 利润回填（products.profit_rate）
    profit_filled = (await session.execute(
        select(func.count()).select_from(Product).where(Product.profit_rate.is_not(None))
    )).scalar() or 0
    profit_today = (await session.execute(
        select(func.count()).select_from(Product)
        .where(Product.profit_rate.is_not(None), Product.updated_at >= today_start, Product.updated_at < tomorrow_start)
    )).scalar() or 0
    sources.append(SyncSourceStat(
        key="profit", label="利润回填", count=profit_today,
        status="ok" if profit_today else "none",
        detail=f"已有利润率 {profit_filled} 个产品",
    ))

    # ACOS 回填（products.acos_30d）
    acos_filled = (await session.execute(
        select(func.count()).select_from(Product).where(Product.acos_30d.is_not(None))
    )).scalar() or 0
    acos_today = (await session.execute(
        select(func.count()).select_from(Product)
        .where(Product.acos_30d.is_not(None), Product.updated_at >= today_start, Product.updated_at < tomorrow_start)
    )).scalar() or 0
    sources.append(SyncSourceStat(
        key="acos", label="ACOS回填", count=acos_today,
        status="ok" if acos_today else "none",
        detail=f"已有ACOS {acos_filled} 个产品",
    ))

    # 等级覆盖（基础数据分析结果）
    level_filled = (await session.execute(
        select(func.count()).select_from(Product).where(Product.product_level.is_not(None))
    )).scalar() or 0
    level_empty = (await session.execute(
        select(func.count()).select_from(Product).where(Product.product_level.is_(None))
    )).scalar() or 0
    sources.append(SyncSourceStat(
        key="product_level", label="产品等级", count=level_filled,
        status="ok" if level_empty == 0 else "warn",
        detail=f"已定级 {level_filled}，待定级 {level_empty}",
    ))

    return SyncOverviewResponse(date=today.isoformat(), tasks=tasks, sources=sources)
