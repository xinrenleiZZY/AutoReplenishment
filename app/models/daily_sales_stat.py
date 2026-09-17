"""逐日销量表 — 领星 sales-statistics/report/list 按日区间实抓落库

数据源：https://gw.lingxingerp.com/sales-statistics/report/list（filterDateType=day）
每次请求返回每个 ASIN 一条聚合记录，其 trend_data.main 为 {日期: {value: 单日销量}} 的逐日字典，
一次请求即可拿到整段区间所有单日销量。

按 (asin, stat_date) 幂等：同一 ASIN 同一天只保留最新一次抓取。
只存储系统跟踪的产品（products.status=True）的逐日销量。
"""

from datetime import date, datetime
from sqlalchemy import String, Integer, BigInteger, Date, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.database import Base


class DailySalesStat(Base):
    __tablename__ = "daily_sales_stats"
    __table_args__ = (
        UniqueConstraint("asin", "stat_date", name="uq_daily_stat_asin_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    stat_date: Mapped[date] = mapped_column(Date, index=True, comment="统计日期")
    sales_qty: Mapped[int] = mapped_column(Integer, default=0, comment="单日销量")
    data_source: Mapped[str] = mapped_column(String(20), default="lx", comment="数据来源")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
