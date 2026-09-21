"""缓存天数语义分类 API 路由（仅展示，增删改查暂不支持）"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.semantic_classification import SemanticClassification
from app.schemas.reference import SemanticClassificationPage

router = APIRouter()


@router.get("", response_model=SemanticClassificationPage)
async def list_semantic_classifications(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
):
    """获取语义分类列表（按更新时间倒序，数据库分页，返回 total+items）"""
    total = (await session.execute(
        select(func.count()).select_from(SemanticClassification)
    )).scalar() or 0

    result = await session.execute(
        select(SemanticClassification)
        .order_by(SemanticClassification.updated_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return {"total": total, "items": result.scalars().all()}
