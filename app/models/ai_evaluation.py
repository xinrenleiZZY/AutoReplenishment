"""DeepSeek AI 评估结果表（采购决策辅助，可追溯）"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Date, DateTime, BigInteger, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AiEvaluation(Base):
    __tablename__ = "ai_evaluations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码（日报用 __daily__）")
    calc_date: Mapped[date] = mapped_column(Date, index=True, comment="评估日期")
    eval_type: Mapped[str] = mapped_column(String(30), comment="purchase_advice/daily_report")
    input_data: Mapped[str | None] = mapped_column(Text, comment="输入快照JSON")
    output_data: Mapped[str | None] = mapped_column(Text, comment="输出结果JSON")
    model: Mapped[str | None] = mapped_column(String(50), comment="模型")
    status: Mapped[str] = mapped_column(String(10), default="success", comment="success/failed")
    error_message: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    latency_ms: Mapped[int | None] = mapped_column(Integer, comment="耗时毫秒")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
