"""后台任务持久化表（Phase 3 / B-04）

背景：计算任务/任务大厅任务的登记原先只在进程内存里（`_jobs` / `_single_tasks`），
API 重启后任务状态与历史全部丢失。本表把任务落库：可查历史、可看进度、
重启后可识别"被中断"的任务，并可按 kind 恢复未到期的定时任务。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskJob(Base):
    __tablename__ = "task_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, comment="任务ID")
    kind: Mapped[str | None] = mapped_column(
        String(40), index=True,
        comment="来源 batch/due/level/single/task-hall-multi/task-hall-single",
    )
    status: Mapped[str] = mapped_column(
        String(12), default="running", index=True,
        comment="running/done/failed/cancelled/interrupted/scheduled",
    )
    operator: Mapped[str | None] = mapped_column(String(60), comment="发起人（前端传入）")
    params_json: Mapped[str | None] = mapped_column(Text, comment="入参快照")
    result_json: Mapped[str | None] = mapped_column(Text, comment="结果/统计快照")
    progress_json: Mapped[str | None] = mapped_column(Text, comment="进度快照")
    error_message: Mapped[str | None] = mapped_column(Text, comment="失败原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
