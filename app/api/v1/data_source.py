"""数据来源核验字典 API 路由"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.data_source_dict import DataSourceDict
from app.schemas.data_source import (
    DataSourceDictResponse,
    DataSourceDictCreate,
    DataSourceDictUpdate,
    DataSourceDictBatchItem,
)
from app.services.data_source_scan import scan_data_source
from app.services.field_usage import get_usage_counter, refresh_usage_counter

router = APIRouter()


@router.get("", response_model=List[DataSourceDictResponse])
async def list_data_source_dict(
    category: Optional[str] = Query(None, description="按分组精确过滤"),
    q: Optional[str] = Query(None, description="表名/字段名/说明模糊搜索"),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
):
    """获取数据来源字典列表（按分组+排序）"""
    stmt = select(DataSourceDict)
    if category:
        stmt = stmt.where(DataSourceDict.category == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            DataSourceDict.table_name.ilike(like)
            | DataSourceDict.field_name.ilike(like)
            | DataSourceDict.field_comment.ilike(like)
        )
    stmt = stmt.order_by(
        DataSourceDict.category,
        DataSourceDict.sort_order,
        DataSourceDict.table_name,
        DataSourceDict.field_name,
    ).offset(skip).limit(limit)
    result = await session.execute(stmt)
    rows = result.scalars().all()
    # 附加字段实际被代码引用的次数（0 = 未使用）
    usage = get_usage_counter()
    for r in rows:
        used = usage.get(r.field_name, 0)
        r.used_count = used
        r.is_used = used > 0
    return rows


@router.get("/categories", response_model=List[str])
async def list_data_source_categories(
    session: AsyncSession = Depends(get_session),
):
    """获取所有存在的分组，用于前端筛选下拉"""
    result = await session.execute(
        select(DataSourceDict.category)
        .where(DataSourceDict.category.isnot(None))
        .distinct()
        .order_by(DataSourceDict.category)
    )
    return result.scalars().all()


@router.post("/scan", response_model=dict)
async def trigger_scan(
    session: AsyncSession = Depends(get_session),
):
    """触发一次字段扫描：遍历所有数据表字段，生成/补全来源字典"""
    try:
        res = await scan_data_source(session)
        refresh_usage_counter()
        return res
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"扫描失败: {e}")


@router.post("", response_model=DataSourceDictResponse, status_code=201)
async def create_data_source_item(
    data: DataSourceDictCreate,
    session: AsyncSession = Depends(get_session),
):
    """新增一条字段来源记录"""
    table_name = (data.table_name or "").strip()
    field_name = (data.field_name or "").strip()
    if not table_name or not field_name:
        raise HTTPException(status_code=400, detail="表名与字段名不能为空")
    item = DataSourceDict(
        table_name=table_name,
        field_name=field_name,
        field_comment=data.field_comment,
        data_source=data.data_source,
        collect_method=data.collect_method,
        update_freq=data.update_freq,
        notes=data.notes,
        category=data.category,
        sort_order=data.sort_order,
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


@router.put("/batch", response_model=List[DataSourceDictResponse])
async def batch_update_data_source(
    data: List[DataSourceDictBatchItem],
    session: AsyncSession = Depends(get_session),
):
    """批量保存：按 id 更新可编辑字段（数据来源/采集方式/更新频率/备注/说明/分组）"""
    if not data:
        return []
    ids = [item.id for item in data]
    rows = (await session.execute(
        select(DataSourceDict).where(DataSourceDict.id.in_(ids))
    )).scalars().all()
    row_map = {r.id: r for r in rows}
    saved = []
    for item in data:
        row = row_map.get(item.id)
        if row is None:
            continue
        # 前端每次提交完整行，统一覆盖，允许清空字段（null 持久化为 NULL）
        row.field_comment = item.field_comment
        row.data_source = item.data_source
        row.collect_method = item.collect_method
        row.update_freq = item.update_freq
        row.notes = item.notes
        row.category = item.category
        row.sort_order = item.sort_order
        saved.append(row)
    await session.commit()
    for r in saved:
        await session.refresh(r)
    return saved


@router.put("/{item_id}", response_model=DataSourceDictResponse)
async def update_data_source_item(
    item_id: int,
    data: DataSourceDictUpdate,
    session: AsyncSession = Depends(get_session),
):
    """更新单条字段来源记录"""
    item = (await session.execute(
        select(DataSourceDict).where(DataSourceDict.id == item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if data.field_comment is not None:
        item.field_comment = data.field_comment
    if data.data_source is not None:
        item.data_source = data.data_source
    if data.collect_method is not None:
        item.collect_method = data.collect_method
    if data.update_freq is not None:
        item.update_freq = data.update_freq
    if data.notes is not None:
        item.notes = data.notes
    if data.category is not None:
        item.category = data.category
    if data.sort_order is not None:
        item.sort_order = data.sort_order
    await session.commit()
    await session.refresh(item)
    return item


@router.delete("/{item_id}")
async def delete_data_source_item(
    item_id: int,
    session: AsyncSession = Depends(get_session),
):
    """删除一条字段来源记录"""
    item = (await session.execute(
        select(DataSourceDict).where(DataSourceDict.id == item_id)
    )).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await session.delete(item)
    await session.commit()
    return {"message": "已删除"}
