"""基础数据（节日/工期/季节曲线/同步日志）响应模型"""

from datetime import datetime

from pydantic import BaseModel


class FestivalCalendarResponse(BaseModel):
    id: int
    festival: str
    festival_en: str | None = None
    listing_start: datetime | None = None
    festival_date: datetime | None = None
    festival_end: datetime | None = None
    hot_period: str | None = None
    hot_start_month: int | None = None
    hot_end_month: int | None = None
    launch_stage: str | None = None
    growth_stage: str | None = None
    mature_stage: str | None = None
    decline_stage: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class CategoryLeadtimeResponse(BaseModel):
    id: int
    level1_category: str
    level2_category: str | None = None
    lead_time_min: int | None = None
    lead_time_max: int | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class SeasonalCurveResponse(BaseModel):
    id: int
    festival: str
    sub_category: str
    month_distribution: str
    sample_count: int = 0
    sample_asins: str | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SyncLogResponse(BaseModel):
    id: int
    sync_type: str
    status: str
    total_count: int | None = None
    success_count: int | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}
