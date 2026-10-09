"""数据同步日志 API 路由"""

import asyncio
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.sync_log import SyncLog
from app.schemas.reference import SyncLogResponse
from app.tasks import sync_tasks

router = APIRouter()


SYNC_RUNNERS = {
    "product": "sync_products",
    "sales": "sync_sales_data",
    "inventory": "sync_inventory",
    "box_quantity": "sync_box_quantity",
    "purchase_orders": "sync_purchase_orders",
    "sales_statistics": "sync_sales_statistics",
    "fx_rate": "sync_fx_rate",
    "profit": "sync_profit_rates",
    "acos": "sync_acos",
    "monthly_lingxing": "sync_monthly_lingxing",
    "daily_sales": "sync_daily_sales",
    "profit_report": "sync_profit_report",
    "purchase_sources": "sync_purchase_sources",
    "base_analysis": "sync_base_analysis",
    "purchase_order_items": "sync_purchase_order_items",
}


def _get_runner(name: str):
    """从 sync_tasks / scheduler 获取同步函数"""
    if hasattr(sync_tasks, name):
        return getattr(sync_tasks, name)
    from app.tasks import scheduler

    return getattr(scheduler, name)


@router.get("", response_model=List[SyncLogResponse])
async def list_sync_logs(
    sync_type: str | None = Query(None, description="同步类型 sales/inventory/product"),
    status: str | None = Query(None, description="状态 running/success/failed"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """获取同步日志列表（按开始时间倒序）"""
    query = select(SyncLog)
    if sync_type:
        query = query.where(SyncLog.sync_type == sync_type)
    if status:
        query = query.where(SyncLog.status == status)
    query = query.order_by(SyncLog.started_at.desc()).offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/run")
async def trigger_sync(sync_type: str = Query(..., description="同步类型，见 SYNC_RUNNERS")):
    """手动触发一次数据同步（后台执行，结果写入同步日志）"""
    if sync_type not in SYNC_RUNNERS:
        raise HTTPException(status_code=400, detail=f"未知同步类型: {sync_type}，可选 {list(SYNC_RUNNERS)}")

    runner = _get_runner(SYNC_RUNNERS[sync_type])
    asyncio.create_task(runner())
    return {
        "message": f"{sync_type} 同步任务已触发，结果将写入同步日志",
        "sync_type": sync_type,
    }
