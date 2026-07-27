"""同步日志表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_type: Mapped[str] = mapped_column(String(30), comment="同步类型 sales/inventory/product")
    status: Mapped[str] = mapped_column(String(10), default="running", comment="状态 running/success/failed")
    total_count: Mapped[int | None] = mapped_column(Integer, comment="总记录数")
    success_count: Mapped[int | None] = mapped_column(Integer, comment="成功数")
    error_message: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="开始时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间")
