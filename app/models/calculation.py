"""计算结果表"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Float, Date, DateTime, BigInteger, Text, Boolean, UniqueConstraint
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

    # 计算时基础快照（新老品/产品等级/生命周期，随结果落库，便于溯源）
    product_stage: Mapped[str | None] = mapped_column(String(20), comment="计算时新老品（新品/老品）")
    product_level: Mapped[str | None] = mapped_column(String(10), comment="计算时产品等级（S/A/B/C/D）")
    life_cycle: Mapped[str | None] = mapped_column(String(20), comment="计算时生命周期（节日时间点表/SIF）")

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
    base_score: Mapped[float | None] = mapped_column(Float, comment="原始公式评分（特例加订/不加订前）")
    purchase_level: Mapped[str | None] = mapped_column(String(20), comment="立即采购/观察/暂停")
    score_detail: Mapped[str | None] = mapped_column(Text, comment="各因素得分明细 JSON")

    # 运营确认
    operator_confirmed: Mapped[str | None] = mapped_column(String(50), comment="确认人")
    confirmed_qty: Mapped[int | None] = mapped_column(Integer, comment="确认采购数量")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="确认时间")

    # 人工反馈（对本次计算结果的评价/纠偏，供后续 AI 评估参考）
    user_feedback: Mapped[str | None] = mapped_column(Text, comment="人工反馈内容")
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime, comment="反馈时间")

    # 结果采纳（运营确认该日/该ASIN 分析正确，用于准确率统计）
    adopted: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否采纳(分析正确)")
    adopted_at: Mapped[datetime | None] = mapped_column(DateTime, comment="采纳时间")
    adopted_by: Mapped[str | None] = mapped_column(String(50), comment="采纳人")
    adopted_confidence: Mapped[float | None] = mapped_column(Float, comment="采纳置信度(%)")

    # 领星口径库存交叉验证（亚马逊FBA可售天数/预估日销，与公式结果互验）
    lx_available_days: Mapped[int | None] = mapped_column(Integer, comment="领星FBA可售天数(交叉验证)")
    lx_stockout_date: Mapped[date | None] = mapped_column(Date, comment="领星预计售罄日期")
    lx_estimated_daily_sales: Mapped[float | None] = mapped_column(Float, comment="领星预估日销量")
    logic_version: Mapped[int] = mapped_column(Integer, default=0, comment="计算逻辑版本：1=最新（AI复核/盈利门槛/建议量修复）")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")


class CalculationTimelineReset(Base):
    """排程重置(时间调节器)：临时覆盖某 ASIN 的"有效最近计算日期"，用于把下次分析时间线整体拨快/拨慢。

    只作用于调度层(下次分析等级/到期判断)，不改真实计算结果与频率配置。
    - override_last_date: 调度层读取的"有效最近计算日期"；
    - target_date: 预设期望的下次到期日；真实计算日期一旦 >= target_date 即自动过期删除，恢复常态排程。
    """
    __tablename__ = "calculation_timeline_resets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    override_last_date: Mapped[date] = mapped_column(Date, comment="排程层使用的有效最近计算日期")
    target_date: Mapped[date] = mapped_column(Date, comment="期望下次到期日；真实计算超过该日即自动过期")
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


class CalculationSkipLog(Base):
    """静默跳过记录 — 分析前门禁拦截、未生成 calculation_results 的 ASIN 留痕（便于排查）

    与 CalculationStepResult 的区别：跳过时不存在 calculation_results 主记录，
    故单独建表，仅记录门禁类型/判定依据，不参与页面展示与日报统计。
    """

    __tablename__ = "calculation_skip_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码")
    calc_date: Mapped[date] = mapped_column(Date, index=True, comment="判定日期")
    gate: Mapped[str] = mapped_column(String(30), comment="门禁类型（节日门禁）")
    festival: Mapped[str | None] = mapped_column(String(50), comment="产品节日(products.festival)")
    festival_name: Mapped[str | None] = mapped_column(String(100), comment="匹配到的节日时间点表节日名")
    next_festival_date: Mapped[date | None] = mapped_column(Date, comment="下一次节日日期(festival_calendar.festival_date)")
    days_until: Mapped[int | None] = mapped_column(Integer, comment="距下一次节日天数")
    reason: Mapped[str | None] = mapped_column(Text, comment="跳过原因")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
