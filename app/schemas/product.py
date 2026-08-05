"""产品相关 Pydantic 模型"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    asin: str = Field(..., max_length=20)
    product_name: str = Field(..., max_length=200)
    category: Optional[str] = None
    sub_category: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    operator: Optional[str] = None
    profit_rate: Optional[float] = None


class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    status: Optional[bool] = None
    operator: Optional[str] = None
    profit_rate: Optional[float] = None
    life_cycle: Optional[str] = None
    product_level: Optional[str] = None
    calc_frequency: Optional[str] = None


class ProductResponse(BaseModel):
    asin: str
    product_name: str
    category: Optional[str] = None
    sub_category: Optional[str] = None
    life_cycle: Optional[str] = None
    product_level: Optional[str] = None
    calc_frequency: Optional[str] = None
    product_type: Optional[str] = None
    product_stage: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    status: bool
    operator: Optional[str] = None
    profit_rate: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
