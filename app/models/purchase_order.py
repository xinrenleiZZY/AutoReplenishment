# -*- coding: utf-8 -*-
"""采购单主表 — 领星 采购单产品信息 orderListsV2 网页API 抓取落库（外层 list 每张采购单一行）

数据源：https://huizhixin.lingxing.com/api/purchase/orderListsV2
接口返回 data.list 为采购单数组，本表按「采购单」逐行落库；明细见 purchase_order_item
（purchase_order_item.purchase_order_id 关联本表 id）。字段名沿用接口原字段。

  - id 为主键，取接口 list[].id（非自增）。
  - 金额统一 Numeric(18,4)；tinyint 用 SmallInteger；json 字段用 JSONB。
  - 空时间（接口返回 ''）统一存 null。
  - req_id 为接口顶层 require_id（当前转发通道未回传则存 null）。
  - create_at 为业务外新增的落库时间。
"""

from datetime import date, datetime

from sqlalchemy import String, Integer, BigInteger, SmallInteger, DateTime, Date, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchaseOrder(Base):
    __tablename__ = "purchase_order"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False, comment="订单id(list[].id)")
    order_id: Mapped[str | None] = mapped_column(String(64), index=True, comment="订单order_id 1_PO260924014")
    order_sn: Mapped[str | None] = mapped_column(String(64), index=True, comment="采购单号 PO260924014")
    custom_order_sn: Mapped[str | None] = mapped_column(String(64), comment="自定义单号")
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="供应商ID")
    supplier_name: Mapped[str | None] = mapped_column(String(255), comment="供应商名称")
    is_supplier_auth: Mapped[int | None] = mapped_column(SmallInteger, comment="是否供应商认证 1/0")
    is_tax: Mapped[int | None] = mapped_column(SmallInteger, comment="是否含税 1/0")
    purchase_type: Mapped[int | None] = mapped_column(SmallInteger, comment="采购类型编码")
    purchase_type_text: Mapped[str | None] = mapped_column(String(64), comment="采购类型文字")
    qc_type: Mapped[int | None] = mapped_column(SmallInteger, comment="质检类型编码")
    qc_type_text: Mapped[str | None] = mapped_column(String(64), comment="质检类型文字")
    wid: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="仓库ID")
    ware_house_name: Mapped[str | None] = mapped_column(String(255), comment="仓库名称")
    status: Mapped[int | None] = mapped_column(Integer, index=True, comment="订单状态编码")
    status_text: Mapped[str | None] = mapped_column(String(64), index=True, comment="订单状态文本")
    purchase_sign_status: Mapped[int | None] = mapped_column(SmallInteger, comment="签收状态编码，允许null")
    purchase_sign_status_text: Mapped[str | None] = mapped_column(String(64), comment="签收状态文本")
    purchase_sign_time: Mapped[datetime | None] = mapped_column(DateTime, comment="签收时间，空存null")
    shipping_price: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="运费金额")
    shipping_currency: Mapped[str | None] = mapped_column(String(16), comment="运费币种")
    amount_total: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="订单总金额")
    standard_amount_total: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="标准金额，可为null")
    source: Mapped[int | None] = mapped_column(Integer, comment="来源")
    process_mode: Mapped[int | None] = mapped_column(SmallInteger, comment="加工方式编码")
    process_mode_text: Mapped[str | None] = mapped_column(String(64), comment="加工方式文字")
    pay_status: Mapped[int | None] = mapped_column(Integer, comment="付款状态编码")
    pay_status_text: Mapped[str | None] = mapped_column(String(64), comment="付款状态文字")
    create_uid: Mapped[int | None] = mapped_column(BigInteger, comment="创建人uid")
    create_realname: Mapped[str | None] = mapped_column(String(128), comment="创建人名")
    create_time: Mapped[date | None] = mapped_column(Date, index=True, comment="创建日期")
    finish_time: Mapped[datetime | None] = mapped_column(DateTime, comment="完成时间，空null")
    total_product_gross_weight: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="总毛重")
    order_time: Mapped[datetime | None] = mapped_column(DateTime, comment="下单时间")
    remark: Mapped[str | None] = mapped_column(Text, comment="备注")
    sub_status: Mapped[int | None] = mapped_column(Integer, comment="子状态编码")
    sub_status_text: Mapped[str | None] = mapped_column(String(64), comment="子状态文本")
    other_currency: Mapped[str | None] = mapped_column(String(16), comment="其他费用币种")
    other_fee: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="其他费用")
    purchase_currency: Mapped[str | None] = mapped_column(String(16), comment="采购币种")
    purchase_rate: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="采购汇率")
    fee_part_type: Mapped[int | None] = mapped_column(SmallInteger, comment="费用分摊类型编码")
    fee_part_type_text: Mapped[str | None] = mapped_column(String(64), comment="费用分摊类型文字")
    settlement_method: Mapped[int | None] = mapped_column(Integer, comment="结算方式编码")
    settlement_method_text: Mapped[str | None] = mapped_column(String(64), comment="结算方式文字")
    settlement_description: Mapped[str | None] = mapped_column(Text, comment="结算说明")
    payment_method: Mapped[str | None] = mapped_column(String(64), comment="付款方式编码")
    payment_method_text: Mapped[str | None] = mapped_column(String(64), comment="付款方式文字")
    purchaser_id: Mapped[int | None] = mapped_column(BigInteger, comment="采购人id")
    purchaser_name: Mapped[str | None] = mapped_column(String(128), comment="采购人名称")
    alibaba_order_sn: Mapped[str | None] = mapped_column(String(128), comment="阿里订单号，null")
    alibaba_account_name: Mapped[str | None] = mapped_column(String(255), comment="阿里账号")
    alibaba_order_id: Mapped[str | None] = mapped_column(String(128), comment="阿里订单id")
    alibaba_order_url: Mapped[str | None] = mapped_column(String(512), comment="阿里链接")
    alibaba_related_type: Mapped[int | None] = mapped_column(Integer, comment="阿里关联类型")
    seller_message: Mapped[str | None] = mapped_column(Text, comment="卖家消息，null")
    trade_method: Mapped[str | None] = mapped_column(String(64), comment="交易方式编码，null")
    trade_method_text: Mapped[str | None] = mapped_column(String(128), comment="交易方式文本")
    post_fee: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="邮费，null")
    refund_payment: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="退款金额，null")
    total_success_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="成功金额")
    is_amount_equal: Mapped[int | None] = mapped_column(SmallInteger, comment="金额是否一致 1/0")
    is_goods_value_equal: Mapped[int | None] = mapped_column(SmallInteger, comment="货值是否一致")
    has_alibaba_amount_diff: Mapped[int | None] = mapped_column(SmallInteger, comment="阿里金额差异 bool转1/0")
    opt_uid: Mapped[int | None] = mapped_column(BigInteger, comment="操作人uid")
    opt_realname: Mapped[str | None] = mapped_column(String(128), comment="操作人名")
    total_price: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="总价")
    other_currency_icon: Mapped[str | None] = mapped_column(String(16), comment="币种符号")
    shipping_currency_icon: Mapped[str | None] = mapped_column(String(16), comment="运费币种符号")
    purchase_currency_icon: Mapped[str | None] = mapped_column(String(16), comment="采购币种符号")
    quantity_total: Mapped[int | None] = mapped_column(Integer, comment="订单总数量")
    quantity_receive: Mapped[int | None] = mapped_column(Integer, comment="已收货数量")
    quantity_entry: Mapped[int | None] = mapped_column(Integer, comment="入库数量")
    quantity_real: Mapped[int | None] = mapped_column(Integer, comment="实际数量")
    quantity_return: Mapped[int | None] = mapped_column(Integer, comment="退货数量")
    quantity_exchange: Mapped[int | None] = mapped_column(Integer, comment="换货数量")
    op_types: Mapped[dict | None] = mapped_column(JSONB, comment="操作权限对象 {audit,cancel...}")
    can_quick_storage: Mapped[int | None] = mapped_column(SmallInteger, comment="是否可快速入库")
    is_changing: Mapped[int | None] = mapped_column(SmallInteger, comment="是否变更中")
    is_urgent: Mapped[int | None] = mapped_column(SmallInteger, comment="是否紧急")
    item_num: Mapped[int | None] = mapped_column(Integer, comment="明细行数")
    is_overdue: Mapped[int | None] = mapped_column(SmallInteger, comment="是否逾期")
    overdue_days: Mapped[int | None] = mapped_column(Integer, comment="逾期天数")
    inbound_qc_num: Mapped[int | None] = mapped_column(Integer, comment="入库质检数量")
    return_status: Mapped[int | None] = mapped_column(Integer, comment="退货状态")
    in_stock_amounts: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="入库金额")
    return_amounts: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="退货金额")
    discount_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="折扣金额")
    pay_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="已付金额")
    receive_amounts: Mapped[float | None] = mapped_column(Numeric(18, 4), comment="收货金额")
    quantity_noticed: Mapped[int | None] = mapped_column(Integer, comment="已通知数量")
    quantity_unnotice: Mapped[int | None] = mapped_column(Integer, comment="未通知数量")
    logistics_info: Mapped[list | None] = mapped_column(JSONB, comment="物流信息数组")
    logistics_list: Mapped[list | None] = mapped_column(JSONB, comment="物流列表数组")
    custom_fields: Mapped[dict | None] = mapped_column(JSONB, comment="自定义字段（预计发货时间在这里）")
    audit_info: Mapped[str | None] = mapped_column(Text, comment="审批人信息文本")
    req_id: Mapped[str | None] = mapped_column(String(128), comment="接口返回顶层 require_id，用于追踪请求")
    create_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="数据落库时间（业务外，新增）")
