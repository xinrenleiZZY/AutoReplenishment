"""产品分类工期表 - 存储产品分类与工期的映射关系"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CategoryLeadtime(Base):
    __tablename__ = "category_leadtimes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level1_category: Mapped[str] = mapped_column(String(100), index=True, comment="一级分类")
    level2_category: Mapped[str | None] = mapped_column(String(100), comment="二级分类")
    lead_time_min: Mapped[int | None] = mapped_column(Integer, comment="工期最小值（天）")
    lead_time_max: Mapped[int | None] = mapped_column(Integer, comment="工期最大值（天）")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
