"""配置参数表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConfigParam(Base):
    __tablename__ = "config_params"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    param_key: Mapped[str] = mapped_column(String(100), unique=True, comment="配置键")
    param_value: Mapped[str] = mapped_column(Text, comment="配置值 JSON")
    description: Mapped[str | None] = mapped_column(String(200), comment="说明")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
