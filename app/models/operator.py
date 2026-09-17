"""运营人员表"""

from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="ID")
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, comment="姓名")
    role: Mapped[str | None] = mapped_column(String(50), comment="岗位/角色")
    status: Mapped[bool] = mapped_column(Boolean, default=True, comment="启用/停用")
    feishu_user_id: Mapped[str | None] = mapped_column(String(100), comment="飞书用户ID（日报@用）")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
