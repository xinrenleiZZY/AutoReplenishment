"""产品分类工期 API 路由"""

from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.category_leadtime import CategoryLeadtime
from app.schemas.reference import CategoryLeadtimeResponse

router = APIRouter()


@router.get("/", response_model=List[CategoryLeadtimeResponse])
async def list_category_leadtimes(
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """获取分类工期列表（按一级分类排序）"""
    result = await session.execute(
        select(CategoryLeadtime)
        .order_by(CategoryLeadtime.level1_category, CategoryLeadtime.level2_category)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()
