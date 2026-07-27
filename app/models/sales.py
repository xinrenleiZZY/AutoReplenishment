"""销量数据表"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Date, DateTime, BigInteger, Float, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.database import Base


class SalesData(Base):
    __tablename__ = "sales_data"
    __table_args__ = (
        UniqueConstraint("asin", "date", name="uq_asin_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    date: Mapped[date] = mapped_column(Date, comment="销售日期")
    sales_qty: Mapped[int] = mapped_column(Integer, default=0, comment="销量")
    sales_amount: Mapped[float | None] = mapped_column(Float, comment="销售额")
    data_source: Mapped[str] = mapped_column(String(20), default="lx", comment="数据来源")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
