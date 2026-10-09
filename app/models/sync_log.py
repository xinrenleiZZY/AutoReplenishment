"""同步日志表"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncLog(Base):
    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_type: Mapped[str] = mapped_column(String(30), comment="同步类型 sales/inventory/product")
    # 状态机（Phase 1 / G-16）：running / success / partial / failed / interrupted / skipped
    status: Mapped[str] = mapped_column(String(12), default="running",
                                       comment="状态 running/success/partial/failed/interrupted/skipped")
    total_count: Mapped[int | None] = mapped_column(Integer, comment="总记录数")
    success_count: Mapped[int | None] = mapped_column(Integer, comment="成功数")
    error_message: Mapped[str | None] = mapped_column(Text, comment="错误信息（部分成功时为失败步骤摘要）")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="开始时间")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间")

    # ── 可观测性扩展（Phase 1 / G-16）──
    run_id: Mapped[str | None] = mapped_column(String(40), index=True,
                                              comment="一次运行/批次的关联ID")
    level: Mapped[str | None] = mapped_column(String(10), default="task",
                                             comment="记录层级 task=任务级 / step=步骤级")
    step: Mapped[str | None] = mapped_column(String(40), comment="步骤名（步骤级记录）")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, comment="父记录id（步骤级→任务级）")
    source: Mapped[str | None] = mapped_column(String(40), comment="数据来源/通道，如 station/mcp/direct")
    stats_json: Mapped[str | None] = mapped_column(Text, comment="脚本返回的统计明细 JSON（含各步骤条数与错误）")
    duration_ms: Mapped[int | None] = mapped_column(Integer, comment="耗时（毫秒）")
    retry_of: Mapped[int | None] = mapped_column(BigInteger, comment="重试前失败记录的 id")
