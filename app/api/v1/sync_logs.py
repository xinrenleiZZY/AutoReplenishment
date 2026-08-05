"""数据同步日志 API 路由"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.sync_log import SyncLog
from app.schemas.reference import SyncLogResponse

router = APIRouter()


@router.get("/", response_model=List[SyncLogResponse])
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
