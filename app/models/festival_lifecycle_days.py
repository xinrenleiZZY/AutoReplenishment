"""节日生命周期天数缓存表 - 预计算每个节日在指定年份的各生命周期阶段实际天数

由 scripts/compute_festival_lifecycle_days.py 回填，等级评定 run_assign_levels 直接读取。
字段命名与 festival_calendar 阶段字段一致（launch_stage/growth_stage/hot_period/mature_stage/decline_stage）。
"""

from datetime import datetime

from sqlalchemy import String, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FestivalLifecycleDays(Base):
    __tablename__ = "festival_lifecycle_days"
    __table_args__ = (
        UniqueConstraint("festival", "year", name="uq_festival_lifecycle_year"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    festival: Mapped[str] = mapped_column(String(100), index=True, comment="节日名称")
    year: Mapped[int] = mapped_column(Integer, comment="年份")
    launch_stage: Mapped[int] = mapped_column(Integer, default=0, comment="启动期天数")
    growth_stage: Mapped[int] = mapped_column(Integer, default=0, comment="增长期天数")
    hot_period: Mapped[int] = mapped_column(Integer, default=0, comment="热卖期天数")
    mature_stage: Mapped[int] = mapped_column(Integer, default=0, comment="成熟期天数")
    decline_stage: Mapped[int] = mapped_column(Integer, default=0, comment="下降期天数")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
