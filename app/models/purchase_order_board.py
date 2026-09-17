# -*- coding: utf-8 -*-
"""采购单看板表 — 领星 采购单看板 purchaseOrderBoard 网页API 每日抓取落库

数据源：https://huizhixin.lingxing.com/api/purchase_report/purchaseOrderBoard
接口返回 data 为 {list: [...], summary: {...}, total: N}，list 为明细数组。本表逐行落库。

  - 每个 list 元素一行：记录采购单号 purchase_order_sn、关联计划编号 relation_plan、
    采购数量 purchase_quantity、已收 receive_quantity、待到货 quantity_diff、待发货 wait_quantity 等。
  - 品名 product_name、ASIN(model) 等均来自看板接口本身，直接落库。
  - 幂等：每次全量抓取先删旧数据再写入（每天完整数据入库，每天更新）。
  - 保存 fetch_date（抓取日期）以便按天全量刷新；另存 raw_data 兜底。
"""

from datetime import date, datetime

from sqlalchemy import String, Integer, BigInteger, DateTime, Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchaseOrderBoard(Base):
    __tablename__ = "purchase_order_board"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    purchase_order_sn: Mapped[str | None] = mapped_column(String(50), index=True, comment="采购单号")
    relation_plan: Mapped[str | None] = mapped_column(String(50), index=True, comment="关联采购计划编号")
    # ── 商品 ──
    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="领星商品ID")
    asin: Mapped[str | None] = mapped_column(String(20), index=True, comment="ASIN(model)")
    product_name: Mapped[str | None] = mapped_column(Text, comment="品名")
    sku: Mapped[str | None] = mapped_column(String(100), comment="本地SKU")
    msku: Mapped[str | None] = mapped_column(String(100), comment="MSKU")
    fnsku: Mapped[str | None] = mapped_column(String(50), comment="FNSKU")
    # ── 数量 ──
    purchase_quantity: Mapped[int | None] = mapped_column(Integer, comment="采购数量")
    receive_quantity: Mapped[int | None] = mapped_column(Integer, comment="已收数量")
    quantity_diff: Mapped[int | None] = mapped_column(Integer, comment="待到货量(采购数-已收数)")
    wait_quantity: Mapped[int | None] = mapped_column(Integer, comment="待发货数量")
    status: Mapped[int | None] = mapped_column(Integer, comment="状态编码")
    expect_arrive_time: Mapped[str | None] = mapped_column(String(50), comment="预计到货时间")
    create_time: Mapped[str | None] = mapped_column(String(50), comment="创建时间(原始)")
    finish_time: Mapped[str | None] = mapped_column(String(50), comment="完结时间(原始)")
    country: Mapped[str | None] = mapped_column(String(50), comment="国家/站点")
    # 原始完整数据（兜底，保证不丢字段）
    raw_data: Mapped[str | None] = mapped_column(Text, comment="单条原始响应JSON")
    fetch_date: Mapped[date] = mapped_column(Date, index=True, comment="抓取日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
