# -*- coding: utf-8 -*-
"""经营利润报表毛利率表 — 领星 bd/profit/report/report/asin/list 网页API 每日抓取落库

数据源：https://gw.lingxingerp.com/bd/profit/report/report/asin/list
功能：按单日（startDate=endDate）抓取全部 ASIN 的经营利润报表，提取毛利率 grossRate 等字段落库。

  - 需求文档口径：data.records.asins 对应系统 ASIN，data.records.grossRate 即毛利率（0~1，如 0.3806=38.06%）。
  - 每个 record 完整原始数据另存 raw_data 兜底（不丢任何字段）。
  - 幂等：同一 ASIN 同一统计日期（stat_date）只保留最新一次抓取（按 stat_date 删旧写新，每天全量）。
"""

from datetime import date, datetime

from sqlalchemy import String, Integer, BigInteger, Float, Date, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProfitReportStat(Base):
    __tablename__ = "profit_report_stats"
    __table_args__ = (
        UniqueConstraint("asin", "stat_date", name="uq_profit_report_asin_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码(数据源 records.asins)")
    stat_date: Mapped[date] = mapped_column(Date, index=True, comment="统计日期(报表日报)")
    gross_rate: Mapped[float | None] = mapped_column(Float, comment="毛利率(0~1，如0.3806=38.06%)")
    gross_profit: Mapped[float | None] = mapped_column(Float, comment="毛利额$")
    roi: Mapped[float | None] = mapped_column(Float, comment="ROI")
    sales_quantity: Mapped[int | None] = mapped_column(Integer, comment="销量 totalSalesQuantity")
    sales_amount: Mapped[float | None] = mapped_column(Float, comment="销售额$ totalSalesAmount")
    ads_cost: Mapped[float | None] = mapped_column(Float, comment="广告费$ totalAdsCost")
    fba_delivery_fee: Mapped[float | None] = mapped_column(Float, comment="FBA物流费$ totalFbaDeliveryFee")
    platform_fee: Mapped[float | None] = mapped_column(Float, comment="平台费$ platformFee")
    # 原始完整数据（兜底，保证不丢字段）
    raw_data: Mapped[str | None] = mapped_column(Text, comment="单条原始响应JSON")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
