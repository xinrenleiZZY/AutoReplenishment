"""数据来源核验字典 响应/请求模型"""

from datetime import datetime

from pydantic import BaseModel


class DataSourceDictResponse(BaseModel):
    id: int
    table_name: str
    field_name: str
    field_comment: str | None = None
    data_source: str | None = None
    collect_method: str | None = None
    update_freq: str | None = None
    notes: str | None = None
    category: str | None = None
    sort_order: int | None = None
    # 字段实际已被代码引用的次数（0 = 未使用）
    used_count: int | None = None
    is_used: bool | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class DataSourceDictCreate(BaseModel):
    table_name: str
    field_name: str
    field_comment: str | None = None
    data_source: str | None = None
    collect_method: str | None = None
    update_freq: str | None = None
    notes: str | None = None
    category: str | None = None
    sort_order: int | None = None


class DataSourceDictUpdate(BaseModel):
    field_comment: str | None = None
    data_source: str | None = None
    collect_method: str | None = None
    update_freq: str | None = None
    notes: str | None = None
    category: str | None = None
    sort_order: int | None = None


class DataSourceDictBatchItem(BaseModel):
    id: int
    field_comment: str | None = None
    data_source: str | None = None
    collect_method: str | None = None
    update_freq: str | None = None
    notes: str | None = None
    category: str | None = None
    sort_order: int | None = None
