"""ASIN 月度历史统计表：同比/环比分析数据（领星利润报表按月回填 + SIF 竞品趋势）"""

from datetime import datetime
from sqlalchemy import String, Integer, BigInteger, Float, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class HistoricalMonthlyStats(Base):
    __tablename__ = "historical_monthly_stats"
    __table_args__ = (
        UniqueConstraint("asin", "month", "source", name="uq_asin_month_source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码")
    month: Mapped[str] = mapped_column(String(7), index=True, comment="月份 YYYY-MM")
    source: Mapped[str] = mapped_column(String(10), default="lingxing", comment="数据来源: lingxing/sif")

    # 销量/销售额（领星利润报表）
    sale_quantity: Mapped[int] = mapped_column(Integer, default=0, comment="月销量")
    sale_amount: Mapped[float | None] = mapped_column(Float, comment="月销售额(USD)")
    gross_profit: Mapped[float | None] = mapped_column(Float, comment="毛利(USD)")
    gross_margin: Mapped[float | None] = mapped_column(Float, comment="毛利率(0-1)")
    return_quantity: Mapped[int] = mapped_column(Integer, default=0, comment="退货数量")
    return_rate: Mapped[float | None] = mapped_column(Float, comment="退货率(0-1)")

    # 广告（利润报表内含 spend/ad_sales）
    ad_spend: Mapped[float | None] = mapped_column(Float, comment="广告花费(USD)")
    ad_sales: Mapped[float | None] = mapped_column(Float, comment="广告销售额(USD)")
    acos: Mapped[float | None] = mapped_column(Float, comment="广告ACOS(0-1)")

    # SIF 竞品趋势（相对购买量 0-100+，趋势信号非精确销量）
    sif_bought: Mapped[float | None] = mapped_column(Float, comment="SIF月度相对购买量")
    sif_price: Mapped[float | None] = mapped_column(Float, comment="SIF当月价格(USD)")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
