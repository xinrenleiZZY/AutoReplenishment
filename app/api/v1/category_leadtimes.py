"""产品分类工期 API 路由"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.category_leadtime import CategoryLeadtime
from app.schemas.reference import CategoryLeadtimeResponse

router = APIRouter()


class CategoryLeadtimeBody(BaseModel):
    """分类工期新增/更新请求体"""
    level1_category: str
    level2_category: Optional[str] = None
    lead_time_min: Optional[int] = None
    lead_time_max: Optional[int] = None
    notes: Optional[str] = None


@router.get("", response_model=List[CategoryLeadtimeResponse])
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


@router.post("", response_model=CategoryLeadtimeResponse, status_code=201)
async def create_category_leadtime(
    data: CategoryLeadtimeBody,
    session: AsyncSession = Depends(get_session),
):
    """新增分类工期"""
    level1 = (data.level1_category or "").strip()
    if not level1:
        raise HTTPException(status_code=400, detail="一级分类不能为空")
    item = CategoryLeadtime(
        level1_category=level1,
        level2_category=(data.level2_category or "").strip() or None,
        lead_time_min=data.lead_time_min,
        lead_time_max=data.lead_time_max,
        notes=data.notes,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.put("/{item_id}", response_model=CategoryLeadtimeResponse)
async def update_category_leadtime(
    item_id: int,
    data: CategoryLeadtimeBody,
    session: AsyncSession = Depends(get_session),
):
    """更新分类工期"""
    item = (await session.execute(
        select(CategoryLeadtime).where(CategoryLeadtime.id == item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    level1 = (data.level1_category or "").strip()
    if not level1:
        raise HTTPException(status_code=400, detail="一级分类不能为空")
    item.level1_category = level1
    item.level2_category = (data.level2_category or "").strip() or None
    item.lead_time_min = data.lead_time_min
    item.lead_time_max = data.lead_time_max
    item.notes = data.notes
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/{item_id}")
async def delete_category_leadtime(
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """删除分类工期"""
    item = (await session.execute(
        select(CategoryLeadtime).where(CategoryLeadtime.id == item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await session.delete(item)
    await session.commit()
    return {"message": "已删除"}
