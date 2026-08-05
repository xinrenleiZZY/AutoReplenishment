"""计算结果相关 Pydantic 模型"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class CalculationResultResponse(BaseModel):
    id: int
    asin: str
    calc_date: date
    forecast_total: Optional[int] = None
    available_stock: Optional[int] = None
    inventory_days: Optional[int] = None
    replenishment_cycle: Optional[int] = None
    urgency_score: Optional[int] = None
    purchase_trigger: Optional[str] = None
    suggested_qty: Optional[int] = None
    batch_plan: Optional[str] = None
    purchase_score: Optional[float] = None
    purchase_level: Optional[str] = None
    score_detail: Optional[str] = None
    operator_confirmed: Optional[str] = None
    confirmed_qty: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}
