"""销量数据 API 路由"""

from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_session
from app.models.sales import SalesData
from app.schemas.sales import SalesDataResponse

router = APIRouter()


@router.get("/{asin}", response_model=List[SalesDataResponse])
async def get_sales_data(
    asin: str,
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """获取销量数据"""
    query = select(SalesData).where(SalesData.asin == asin)
    if start_date:
        query = query.where(SalesData.date >= start_date)
    if end_date:
        query = query.where(SalesData.date <= end_date)
    query = query.order_by(SalesData.date.desc()).offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()
