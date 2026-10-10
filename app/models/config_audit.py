"""参数变更审计表（Phase 1 / G-12）

记录 `config_params` 的每一次变更：谁（operator）、什么时候（changed_at）、
从什么改成什么（old_value → new_value）、来源（api / script / scheduler / sync）。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ConfigAuditLog(Base):
    __tablename__ = "config_audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    param_key: Mapped[str] = mapped_column(String(60), index=True, comment="参数名")
    old_value: Mapped[str | None] = mapped_column(Text, comment="修改前的值（首次设置为空）")
    new_value: Mapped[str | None] = mapped_column(Text, comment="修改后的值")
    operator: Mapped[str | None] = mapped_column(String(60), comment="操作人（前端传入/脚本名）")
    source: Mapped[str | None] = mapped_column(String(30), comment="来源 api/script/scheduler/sync")
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True,
                                                 comment="变更时间")
