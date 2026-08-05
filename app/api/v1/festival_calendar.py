"""节日日历 API 路由"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.festival_calendar import FestivalCalendar
from app.schemas.reference import FestivalCalendarResponse

router = APIRouter()


@router.get("/", response_model=List[FestivalCalendarResponse])
async def list_festival_calendar(
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """获取节日日历列表（按节日日期排序）"""
    result = await session.execute(
        select(FestivalCalendar)
        .order_by(FestivalCalendar.festival_date, FestivalCalendar.festival)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()
