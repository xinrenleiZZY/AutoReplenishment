"""运营人员相关 Pydantic 模型"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class OperatorCreate(BaseModel):
    name: str = Field(..., max_length=50, description="姓名")
    role: Optional[str] = Field(None, max_length=50, description="岗位/角色")
    feishu_user_id: Optional[str] = Field(None, max_length=100, description="飞书用户ID")
    status: Optional[bool] = True
    notes: Optional[str] = None


class OperatorUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=50)
    role: Optional[str] = Field(None, max_length=50)
    feishu_user_id: Optional[str] = Field(None, max_length=100)
    status: Optional[bool] = None
    notes: Optional[str] = None


class OperatorResponse(BaseModel):
    id: int
    name: str
    role: Optional[str] = None
    status: bool
    feishu_user_id: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
