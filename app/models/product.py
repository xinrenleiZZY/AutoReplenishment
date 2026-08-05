"""产品档案表"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Boolean, Date, DateTime, ARRAY, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Product(Base):
    __tablename__ = "products"

    asin: Mapped[str] = mapped_column(String(20), primary_key=True, comment="ASIN编码")
    product_name: Mapped[str] = mapped_column(Text, nullable=False, comment="产品名称")
    category: Mapped[str | None] = mapped_column(String(100), comment="产品分类（如：毛毡类）")
    sub_category: Mapped[str | None] = mapped_column(String(50), comment="子分类（装饰品/非装饰品）")
    life_cycle: Mapped[str | None] = mapped_column(String(20), comment="生命周期阶段")
    product_level: Mapped[str | None] = mapped_column(String(5), comment="产品等级 S/A/B/C/D")
    calc_frequency: Mapped[str | None] = mapped_column(String(5), comment="计算频率 P0/P1/P2/P3/P4")
    product_type: Mapped[str | None] = mapped_column(String(20), comment="节日产品/长期产品")
    product_stage: Mapped[str | None] = mapped_column(String(10), comment="新品/老品")
    festival: Mapped[str | None] = mapped_column(String(50), comment="所属节日")
    core_months: Mapped[str | None] = mapped_column(Text, comment="核心销售月份 JSON数组 [8,9,10,11,12]")
    lead_time: Mapped[int | None] = mapped_column(Integer, comment="工期(天)")
    box_quantity: Mapped[int | None] = mapped_column(Integer, comment="单箱数量")
    min_order_qty: Mapped[int | None] = mapped_column(Integer, comment="最低采购量")
    list_date: Mapped[date | None] = mapped_column(Date, comment="上架日期")
    status: Mapped[bool] = mapped_column(Boolean, default=True, comment="启用/停用")
    operator: Mapped[str | None] = mapped_column(String(50), comment="负责运营")
    profit_rate: Mapped[float | None] = mapped_column(comment="利润率")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
