"""产品管理 API 路由"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_session
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse

router = APIRouter()


@router.get("/", response_model=List[ProductResponse])
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[bool] = None,
    life_cycle: Optional[str] = None,
    product_level: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """获取产品列表"""
    query = select(Product)
    if status is not None:
        query = query.where(Product.status == status)
    if life_cycle:
        query = query.where(Product.life_cycle == life_cycle)
    if product_level:
        query = query.where(Product.product_level == product_level)
    query = query.offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{asin}", response_model=ProductResponse)
async def get_product(asin: str, session: AsyncSession = Depends(get_session)):
    """获取产品详情"""
    result = await session.execute(select(Product).where(Product.asin == asin))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    return product


@router.post("/", response_model=ProductResponse, status_code=201)
async def create_product(data: ProductCreate, session: AsyncSession = Depends(get_session)):
    """创建产品"""
    product = Product(**data.model_dump())
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@router.put("/{asin}", response_model=ProductResponse)
async def update_product(asin: str, data: ProductUpdate, session: AsyncSession = Depends(get_session)):
    """更新产品信息"""
    result = await session.execute(select(Product).where(Product.asin == asin))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    await session.commit()
    await session.refresh(product)
    return product
