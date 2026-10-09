# -*- coding: utf-8 -*-
"""采购单明细子表 — 领星 采购单产品信息 orderListsV2（item_list 每一行一行）

数据源：https://huizhixin.lingxing.com/api/purchase/orderListsV2
接口返回 data.list[].item_list 为明细数组，本表按「明细 item」逐行落库；
purchase_order_id 关联主表 purchase_order.id。字段名沿用接口原字段。

  - id 为主键，取接口 item_list[].id（非自增）。
  - 金额统一 Numeric(18,4)；tinyint 用 SmallInteger；json 字段用 JSONB。
  - 空时间（接口返回 ''）统一存 null。
"""

from datetime import datetime

from sqlalchemy import (
    String, Integer, BigInteger, SmallInteger, DateTime, Numeric, Text, ForeignKey,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_item"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, comment="明细id(item_list[].id)")
    purchase_order_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("purchase_order.id"), index=True, comment="采购单主表ID（关联purchase_order.id）")
    status: Mapped[int | None] = mapped_column(Integer, index=True, comment="明细状态编码")
    plan_sn: Mapped[str | None] = mapped_column(String(64), index=True, comment="计划单号 PP260923024")
    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="产品ID")
    product_name: Mapped[str | None] = mapped_column(Text, comment="产品名称")
    wid: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="仓库ID")
    ware_house_name: Mapped[str | None] = mapped_column(String(255), comment="仓库名称")
    is_gift: Mapped[int | None] = mapped_column(SmallInteger, comment="是否赠品")
    pic_url: Mapped[str | None] = mapped_column(String(512), comment="图片链接")
    sku: Mapped[str | None] = mapped_column(String(128), index=True, comment="sku编码")
    is_first_purchase: Mapped[int | None] = mapped_column(SmallInteger, comment="是否首采")
    first_purchase_text: Mapped[str | None] = mapped_column(String(64), comment="首采文本")
    fnsku: Mapped[str | None] = mapped_column(String(128), comment="fnsku")
    sid: Mapped[str | None] = mapped_column(String(64), comment="sid")
    tax_rate: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="税率")
    tax_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="税额")
    price: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="采购单价")
    cg_price: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="采购成本价")
    standard_price: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="标准价，null")
    price_without_tax: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="不含税单价")
    amount: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="本行含税金额")
    amount_without_tax: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="本行不含税金额")
    quantity_plan: Mapped[int | None] = mapped_column(Integer, comment="计划数量")
    quantity_real: Mapped[int | None] = mapped_column(Integer, comment="实际数量")
    quantity_entry: Mapped[int | None] = mapped_column(Integer, comment="入库数量")
    quantity_return: Mapped[int | None] = mapped_column(Integer, comment="退货数量")
    quantity_exchange: Mapped[int | None] = mapped_column(Integer, comment="换货数量")
    expect_arrive_time: Mapped[datetime | None] = mapped_column(DateTime, comment="预计到货时间")
    remark: Mapped[str | None] = mapped_column(Text, comment="明细备注")
    item_purchase_remark: Mapped[str | None] = mapped_column(Text, comment="采购明细备注")
    cases_num: Mapped[int | None] = mapped_column(Integer, comment="箱数")
    quantity_per_case: Mapped[int | None] = mapped_column(Integer, comment="每箱数量")
    is_aux: Mapped[int | None] = mapped_column(SmallInteger, comment="是否辅料")
    spu: Mapped[str | None] = mapped_column(String(128), comment="spu编码")
    sku_identifier: Mapped[str | None] = mapped_column(String(128), comment="sku标识")
    spu_name: Mapped[str | None] = mapped_column(String(255), comment="spu名称")
    is_combo: Mapped[int | None] = mapped_column(SmallInteger, comment="是否组合商品")
    is_delete: Mapped[int | None] = mapped_column(SmallInteger, comment="是否删除")
    is_related_process_plan: Mapped[int | None] = mapped_column(SmallInteger, comment="是否关联加工计划")
    seller_name: Mapped[str | None] = mapped_column(String(255), comment="卖家名称")
    country_name: Mapped[str | None] = mapped_column(String(128), comment="国家名称")
    quantity_qc: Mapped[int | None] = mapped_column(Integer, comment="质检数量")
    quantity_qc_prepare: Mapped[int | None] = mapped_column(Integer, comment="待质检数量")
    quantity_qc_none: Mapped[int | None] = mapped_column(Integer, comment="无需质检数量")
    product_good_num: Mapped[int | None] = mapped_column(Integer, comment="良品数")
    product_bad_num: Mapped[int | None] = mapped_column(Integer, comment="不良品数")
    available_status: Mapped[int | None] = mapped_column(Integer, comment="可用状态编码")
    available_status_text: Mapped[str | None] = mapped_column(String(64), comment="可用状态文字")
    product_shelf_num: Mapped[str | None] = mapped_column(String(64), comment="库位号")
    quantity_receive: Mapped[int | None] = mapped_column(Integer, comment="收货数量")
    quantity_diff: Mapped[int | None] = mapped_column(Integer, comment="数量差异")
    finish_reason: Mapped[str | None] = mapped_column(Text, comment="完成原因")
    quantity_unnoticed: Mapped[int | None] = mapped_column(Integer, comment="未通知数量")
    quantity_noticed: Mapped[int | None] = mapped_column(Integer, comment="已通知数量")
    msku: Mapped[list | None] = mapped_column(JSONB, comment="msku数组")
    change_status: Mapped[int | None] = mapped_column(Integer, comment="变更状态")
    return_status: Mapped[int | None] = mapped_column(Integer, comment="退货状态")
    exchange_status: Mapped[int | None] = mapped_column(Integer, comment="换货状态")
    supplier_product_url: Mapped[list | None] = mapped_column(JSONB, comment="供应商产品链接数组")
    attribute: Mapped[list | None] = mapped_column(JSONB, comment="属性数组")
    global_tags: Mapped[list | None] = mapped_column(JSONB, comment="全局标签数组")
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, comment="明细自定义字段")
    product_unit: Mapped[str | None] = mapped_column(String(64), comment="产品单位")
    arrival_time_type: Mapped[int | None] = mapped_column(Integer, comment="到货时间类型")
    spec_info: Mapped[dict | None] = mapped_column(JSONB, comment="箱规对象")
    product_gross_weight: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="单品毛重")
    combo_product_list: Mapped[list | None] = mapped_column(JSONB, comment="组合商品列表")
    combo_product_text: Mapped[str | None] = mapped_column(Text, comment="组合商品文本")
    plan_creator_list: Mapped[list | None] = mapped_column(JSONB, comment="计划创建人列表")
    plan_creator_text: Mapped[str | None] = mapped_column(String(255), comment="计划创建人文本")
