"""数据来源核验字典表 — 记录系统每个数据字段/指标从哪采集、怎么采、多久更新一次。

初始清单通过扫描数据库各表字段自动生成，采集方式与来源在生成时按表/字段预填，
页面支持逐行编辑保存，用于字段级数据溯源与核验。
"""

from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DataSourceDict(Base):
    __tablename__ = "data_source_dict"
    __table_args__ = (
        UniqueConstraint("table_name", "field_name", name="uq_ds_table_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    table_name: Mapped[str] = mapped_column(String(100), index=True, comment="所属表/数据块")
    field_name: Mapped[str] = mapped_column(String(100), comment="字段名/指标名")
    field_comment: Mapped[str | None] = mapped_column(String(255), comment="字段说明/注释")
    data_source: Mapped[str | None] = mapped_column(String(50), comment="数据来源(领星/SIF/MCP/本地计算/AI/系统)")
    collect_method: Mapped[str | None] = mapped_column(String(50), comment="采集方式(网页API/MCP/每日抓取/计算/回填)")
    update_freq: Mapped[str | None] = mapped_column(String(50), comment="更新频率(每日/每周/月度/实时)")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注/计算口径说明")
    category: Mapped[str | None] = mapped_column(String(50), comment="分组(产品/销量/库存/计算/成本/基础数据)")
    sort_order: Mapped[int | None] = mapped_column(Integer, default=0, comment="排序")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
