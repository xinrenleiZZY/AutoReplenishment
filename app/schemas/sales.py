"""销量相关 Pydantic 模型"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel, Field


class SalesDataResponse(BaseModel):
    id: int
    asin: str
    date: date
    sales_qty: int
    sales_amount: Optional[float] = None
    data_source: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SalesSummaryResponse(BaseModel):
    asin: str
    year: int
    month: int
    total_qty: int
    avg_daily_qty: float
