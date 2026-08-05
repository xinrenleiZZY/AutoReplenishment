"""季节销售曲线 API 路由"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.seasonal_curve import SeasonalCurve
from app.schemas.reference import SeasonalCurveResponse

router = APIRouter()


@router.get("/", response_model=List[SeasonalCurveResponse])
async def list_seasonal_curves(
    festival: str | None = Query(None, description="按节日筛选"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """获取季节曲线模板列表"""
    query = select(SeasonalCurve)
    if festival:
        query = query.where(SeasonalCurve.festival == festival)
    query = query.order_by(SeasonalCurve.festival, SeasonalCurve.sub_category).offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()
