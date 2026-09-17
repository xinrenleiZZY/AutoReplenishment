# -*- coding: utf-8 -*-
"""产品成本表覆盖字段（ASIN 维度，可维护/导入）

优先级：product_costs 覆盖值 > 产品档案/配置默认值。
字段均为可空，空=用默认值。
"""

from datetime import datetime

from sqlalchemy import String, Float, Text, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProductCost(Base):
    __tablename__ = "product_costs"

    asin: Mapped[str] = mapped_column(String(20), primary_key=True, comment="ASIN")
    price: Mapped[float | None] = mapped_column(Float, comment="售价$（覆盖）")
    cost_cny: Mapped[float | None] = mapped_column(Float, comment="采购总成本￥（覆盖）")
    exchange_rate: Mapped[float | None] = mapped_column(Float, comment="汇率（覆盖）")
    length_cm: Mapped[float | None] = mapped_column(Float, comment="包装长cm")
    width_cm: Mapped[float | None] = mapped_column(Float, comment="包装宽cm")
    height_cm: Mapped[float | None] = mapped_column(Float, comment="包装高cm")
    weight_kg: Mapped[float | None] = mapped_column(Float, comment="毛重kg")
    freight_sea_cny: Mapped[float | None] = mapped_column(Float, comment="海运运费￥（kg单价或件单价）")
    freight_air_cny: Mapped[float | None] = mapped_column(Float, comment="空派运费￥")
    freight_express_cny: Mapped[float | None] = mapped_column(Float, comment="快递运费￥")
    sorting_fee: Mapped[float | None] = mapped_column(Float, comment="分拣费/FBA费$")
    referral_fee: Mapped[float | None] = mapped_column(Float, comment="佣金$")
    packing_fee: Mapped[float | None] = mapped_column(Float, comment="P卡费$")
    inbound_fee: Mapped[float | None] = mapped_column(Float, comment="入库配置费$")
    storage_fee: Mapped[float | None] = mapped_column(Float, comment="仓储费$")
    ad_fee: Mapped[float | None] = mapped_column(Float, comment="广告费预算$")
    return_loss: Mapped[float | None] = mapped_column(Float, comment="退货成本亏损平摊$")
    over_threshold_loss: Mapped[float | None] = mapped_column(Float, comment="超阈值亏损$")
    misc_fee: Mapped[float | None] = mapped_column(Float, comment="附加费$")
    notes: Mapped[str | None] = mapped_column(Text, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
