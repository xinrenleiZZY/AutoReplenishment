"""基础数据（节日/工期/同步日志）响应模型"""

from datetime import datetime

from pydantic import BaseModel


class FestivalCalendarResponse(BaseModel):
    id: int
    festival: str
    festival_en: str | None = None
    listing_start: datetime | None = None
    festival_date: datetime | None = None
    festival_end: datetime | None = None
    festival_periods: str | None = None
    hot_period: str | None = None
    hot_start_month: int | None = None
    hot_end_month: int | None = None
    launch_stage: str | None = None
    growth_stage: str | None = None
    mature_stage: str | None = None
    decline_stage: str | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class FestivalCalendarCreate(BaseModel):
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


class FestivalCalendarUpdate(BaseModel):
    festival: str | None = None
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


class CategoryLeadtimeResponse(BaseModel):
    id: int
    level1_category: str
    level2_category: str | None = None
    lead_time_min: int | None = None
    lead_time_max: int | None = None
    notes: str | None = None

    model_config = {"from_attributes": True}


class SemanticClassificationResponse(BaseModel):
    asin: str
    listing_title: str | None = None
    semantic_classification: str | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class SemanticClassificationPage(BaseModel):
    total: int
    items: list[SemanticClassificationResponse]


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


class SyncTaskStat(BaseModel):
    """今日同步任务执行情况（按同步类型聚合，取该类型最新一次）"""
    sync_type: str
    label: str
    status: str | None = None  # running/success/failed/none
    total_count: int | None = None
    success_count: int | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class SyncSourceStat(BaseModel):
    """今日某数据源的聚合概览"""
    key: str
    label: str
    count: int = 0
    status: str = "ok"  # ok / warn / none
    detail: str | None = None


class SyncOverviewResponse(BaseModel):
    """今日同步数据聚合概览"""
    date: str
    tasks: list[SyncTaskStat]
    sources: list[SyncSourceStat]
