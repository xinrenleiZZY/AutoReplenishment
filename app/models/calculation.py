"""计算结果表"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Float, Date, DateTime, BigInteger, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.database import Base


class CalculationResult(Base):
    __tablename__ = "calculation_results"
    __table_args__ = (
        UniqueConstraint("asin", "calc_date", name="uq_asin_calc_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    calc_date: Mapped[date] = mapped_column(Date, comment="计算日期")

    # 销量预测
    forecast_months: Mapped[str | None] = mapped_column(Text, comment="未来各月预测销量 JSON")
    forecast_total: Mapped[int | None] = mapped_column(Integer, comment="预测总销量")

    # 库存分析
    available_stock: Mapped[int | None] = mapped_column(Integer, comment="当前可用库存")
    inventory_days: Mapped[int | None] = mapped_column(Integer, comment="库存覆盖天数")
    replenishment_cycle: Mapped[int | None] = mapped_column(Integer, comment="补货周期(天)")
    urgency_score: Mapped[int | None] = mapped_column(Integer, comment="紧急程度评分")

    # 采购决策
    purchase_trigger: Mapped[str | None] = mapped_column(String(20), comment="需要采购/无需采购")
    suggested_qty: Mapped[int | None] = mapped_column(Integer, comment="建议采购数量")
    batch_plan: Mapped[str | None] = mapped_column(Text, comment="批次规划 JSON")

    # 评分模型
    purchase_score: Mapped[float | None] = mapped_column(Float, comment="采购评分")
    purchase_level: Mapped[str | None] = mapped_column(String(20), comment="立即采购/观察/暂停")
    score_detail: Mapped[str | None] = mapped_column(Text, comment="各因素得分明细 JSON")

    # 运营确认
    operator_confirmed: Mapped[str | None] = mapped_column(String(50), comment="确认人")
    confirmed_qty: Mapped[int | None] = mapped_column(Integer, comment="确认采购数量")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="确认时间")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class CalculationStepResult(Base):
    """计算步骤结果表 — 每一步的输入/输出/原因，实现可溯源"""

    __tablename__ = "calculation_step_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    calculation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("calculation_results.id"), index=True,
        comment="所属计算结果ID"
    )
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码")
    calc_date: Mapped[date] = mapped_column(Date, index=True, comment="计算日期")
    step_no: Mapped[int] = mapped_column(Integer, comment="步骤序号")
    step_name: Mapped[str] = mapped_column(String(100), comment="步骤名称")
    status: Mapped[str] = mapped_column(String(20), default="success", comment="success/error/skip")
    input_data: Mapped[str | None] = mapped_column(Text, comment="步骤输入数据 JSON")
    output_data: Mapped[str | None] = mapped_column(Text, comment="步骤输出结果 JSON")
    reason: Mapped[str | None] = mapped_column(Text, comment="判断依据/原因说明")
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="计算时间")
