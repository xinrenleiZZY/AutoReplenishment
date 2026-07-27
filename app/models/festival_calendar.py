"""节日日历表 - 存储节日日期、热卖期、上架开卖时间"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FestivalCalendar(Base):
    __tablename__ = "festival_calendar"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    festival: Mapped[str] = mapped_column(String(100), index=True, comment="节日名称")
    festival_en: Mapped[str | None] = mapped_column(String(100), comment="节日英文名")
    listing_start: Mapped[datetime | None] = mapped_column(DateTime, comment="亚马逊预计上架开卖时间")
    festival_date: Mapped[datetime | None] = mapped_column(DateTime, comment="节日/主题时间")
    festival_end: Mapped[datetime | None] = mapped_column(DateTime, comment="预设节日/主题结束时间")
    hot_period: Mapped[str | None] = mapped_column(String(50), comment="热卖期（如 11-12月）")
    hot_start_month: Mapped[int | None] = mapped_column(Integer, comment="热卖开始月份")
    hot_end_month: Mapped[int | None] = mapped_column(Integer, comment="热卖结束月份")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
