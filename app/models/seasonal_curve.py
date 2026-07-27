"""季节曲线模板表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SeasonalCurve(Base):
    __tablename__ = "seasonal_curves"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    festival: Mapped[str] = mapped_column(String(50), comment="节日")
    sub_category: Mapped[str] = mapped_column(String(50), comment="装饰品/非装饰品/DIY类")
    month_distribution: Mapped[str] = mapped_column(Text, comment="各月销售占比 JSON")
    sample_count: Mapped[int] = mapped_column(Integer, default=0, comment="样本ASIN数量")
    sample_asins: Mapped[str | None] = mapped_column(Text, comment="样本ASIN列表 JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
