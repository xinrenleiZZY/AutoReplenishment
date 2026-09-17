"""计算结果相关 Pydantic 模型"""

from datetime import date, datetime
from typing import Optional
from pydantic import BaseModel


class CalculationResultResponse(BaseModel):
    id: int
    asin: str
    product_name: Optional[str] = None
    product_stage: Optional[str] = None
    product_level: Optional[str] = None
    life_cycle: Optional[str] = None
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
    base_score: Optional[float] = None
    purchase_level: Optional[str] = None
    score_detail: Optional[str] = None
    operator_confirmed: Optional[str] = None
    confirmed_qty: Optional[int] = None
    created_at: datetime
    # 结果采纳（运营确认分析正确）
    adopted: Optional[bool] = None
    adopted_at: Optional[datetime] = None
    adopted_by: Optional[str] = None
    adopted_confidence: Optional[float] = None

    model_config = {"from_attributes": True}


class FeedbackRequest(BaseModel):
    """人工反馈请求：按 ASIN+日期 写入对计算结果的评价/纠偏"""
    asin: str
    calc_date: Optional[date] = None   # 不传则定位该 ASIN 最近一次结果
    feedback: str = ""
    operator: Optional[str] = None      # 反馈人（默认取产品负责人）


class AdoptRequest(BaseModel):
    """采纳请求：按 ASIN+日期 标记该次计算结果为「已采纳（分析正确）」"""
    asin: str
    calc_date: Optional[date] = None   # 不传则定位该 ASIN 最近一次结果
    operator: Optional[str] = None      # 采纳人
    adopted: bool = True                # True=采纳；False=取消采纳
    confidence: Optional[float] = 95.0  # 采纳置信度（分析正确率）


class TimelineResetRequest(BaseModel):
    """排程重置(时间调节器)请求：把下次分析时间线拨到指定等级在 target_date 到期"""
    levels: list[str] = ["S", "A"]      # 要拨快的等级（如 ["S","A"]）
    target_date: Optional[date] = None  # 期望下次到期日，默认明天
