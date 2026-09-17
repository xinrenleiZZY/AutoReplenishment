"""运营人员管理 API 路由"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.operator import Operator
from app.models.product import Product
from app.schemas.operator import OperatorCreate, OperatorUpdate, OperatorResponse
from app.services.operator_sync import sync_operators_from_products, clean_operator_name

router = APIRouter()


@router.get("", response_model=List[OperatorResponse])
async def list_operators(
    keyword: Optional[str] = Query(None, description="按姓名/岗位/备注模糊搜索"),
    status: Optional[bool] = Query(None, description="启用状态筛选；不传返回全部"),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """获取运营人员列表"""
    # 自动从产品负责人字段同步（幂等），无需手工维护
    await sync_operators_from_products(session)
    query = select(Operator)
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(
            or_(
                Operator.name.ilike(like),
                Operator.role.ilike(like),
                Operator.notes.ilike(like),
            )
        )
    if status is not None:
        query = query.where(Operator.status == status)
    query = query.order_by(Operator.status.desc(), Operator.id).offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.post("/sync")
async def trigger_operator_sync(session: AsyncSession = Depends(get_session)):
    """手动触发一次运营人员同步（从产品负责人字段）"""
    stats = await sync_operators_from_products(session)
    return {"message": "运营人员已同步", **stats}


@router.get("/distinct-names")
async def distinct_operator_names(session: AsyncSession = Depends(get_session)):
    """运营名称候选列表：已同步的启用人员（产品负责人清洗后的姓名）"""
    await sync_operators_from_products(session)
    ops = await session.execute(select(Operator.name).where(Operator.status == True))  # noqa: E712
    names = {op for op in ops.scalars().all() if op}
    if not names:  # 兜底：同步异常时直接从产品负责人解析
        rows = await session.execute(
            select(Product.operator).where(Product.operator.isnot(None))
        )
        for (val,) in rows.all():
            for part in str(val).split(","):
                cleaned = clean_operator_name(part)
                if cleaned:
                    names.add(cleaned)
    return {"names": sorted(names)}


@router.get("/{operator_id}", response_model=OperatorResponse)
async def get_operator(operator_id: int, session: AsyncSession = Depends(get_session)):
    """获取单个运营人员"""
    result = await session.execute(select(Operator).where(Operator.id == operator_id))
    operator = result.scalar_one_or_none()
    if not operator:
        raise HTTPException(status_code=404, detail="运营人员不存在")
    return operator


@router.post("/", response_model=OperatorResponse, status_code=201)
async def create_operator(data: OperatorCreate, session: AsyncSession = Depends(get_session)):
    """新增运营人员（姓名唯一）"""
    existed = await session.execute(select(Operator).where(Operator.name == data.name.strip()))
    if existed.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"运营人员 {data.name} 已存在")
    operator = Operator(
        name=data.name.strip(),
        role=data.role.strip() if data.role else None,
        feishu_user_id=data.feishu_user_id.strip() if data.feishu_user_id else None,
        status=data.status if data.status is not None else True,
        notes=data.notes.strip() if data.notes else None,
    )
    session.add(operator)
    await session.commit()
    await session.refresh(operator)
    return operator


@router.put("/{operator_id}", response_model=OperatorResponse)
async def update_operator(
    operator_id: int,
    data: OperatorUpdate,
    session: AsyncSession = Depends(get_session),
):
    """更新运营人员信息"""
    result = await session.execute(select(Operator).where(Operator.id == operator_id))
    operator = result.scalar_one_or_none()
    if not operator:
        raise HTTPException(status_code=404, detail="运营人员不存在")

    payload = data.model_dump(exclude_unset=True)
    if "name" in payload and payload["name"]:
        new_name = payload["name"].strip()
        existed = await session.execute(
            select(Operator).where(Operator.name == new_name, Operator.id != operator_id)
        )
        if existed.scalar_one_or_none():
            raise HTTPException(status_code=409, detail=f"运营人员 {new_name} 已存在")
        operator.name = new_name

    if "role" in payload:
        operator.role = payload["role"].strip() if payload["role"] else None
    if "feishu_user_id" in payload:
        operator.feishu_user_id = payload["feishu_user_id"].strip() if payload["feishu_user_id"] else None
    if "status" in payload:
        operator.status = payload["status"]
    if "notes" in payload:
        operator.notes = payload["notes"].strip() if payload["notes"] else None

    await session.commit()
    await session.refresh(operator)
    return operator


@router.delete("/{operator_id}")
async def delete_operator(operator_id: int, session: AsyncSession = Depends(get_session)):
    """删除运营人员（产品中的负责人名称会保留，仅从人员管理中移除）"""
    result = await session.execute(select(Operator).where(Operator.id == operator_id))
    operator = result.scalar_one_or_none()
    if not operator:
        raise HTTPException(status_code=404, detail="运营人员不存在")
    await session.delete(operator)
    await session.commit()
    return {"message": f"运营人员 {operator.name} 已删除"}
