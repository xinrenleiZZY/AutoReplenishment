# -*- coding: utf-8 -*-
"""领星「产品管理」原始数据表 — product/lists 全字段原样落库（宽表，不做任何清洗）

数据源：https://huizhixin.lingxing.com/api/product/lists
  - 接口返回的 66 个字段各占一列，字段名与接口完全一致（不做改名、不做加工）。
  - id_no 为自增主键（与接口字段 id 无关，id 为领星商品ID，原样落库）。
  - 数组/对象类字段用 JSONB 原样存放；数值型字段按接口语义存 Integer/BigInteger/Numeric。
  - 幂等：每次全量抓取先清空本表再写入（覆盖更新，仅保留最新一份）。
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ProductListsRaw(Base):
    __tablename__ = "product_lists_raw"

    # 自增主键
    id_no: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="自增主键")

    # ── 接口原始 66 字段（名称与 product/lists 返回一致）──
    id: Mapped[int | None] = mapped_column(BigInteger, comment="领星商品ID")
    cid: Mapped[int | None] = mapped_column(Integer, comment="分类ID")
    bid: Mapped[int | None] = mapped_column(Integer, comment="品牌ID")
    sku: Mapped[str | None] = mapped_column(Text, comment="本地SKU")
    sku_identifier: Mapped[str | None] = mapped_column(Text, comment="SKU识别码")
    product_name: Mapped[str | None] = mapped_column(Text, comment="产品名称")
    model: Mapped[str | None] = mapped_column(Text, comment="型号")
    pic_url: Mapped[str | None] = mapped_column(Text, comment="图片地址")
    cg_delivery: Mapped[int | None] = mapped_column(Integer, comment="采购交货周期")
    cg_transport_costs: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="采购运费")
    is_related: Mapped[int | None] = mapped_column(Integer, comment="是否关联")
    primary_supplier_id: Mapped[int | None] = mapped_column(BigInteger, comment="主供应商ID")
    status: Mapped[int | None] = mapped_column(Integer, comment="状态码")
    is_combo: Mapped[int | None] = mapped_column(Integer, comment="是否组合产品")
    open_status: Mapped[int | None] = mapped_column(Integer, comment="启用状态")
    product_type: Mapped[int | None] = mapped_column(Integer, comment="产品类型")
    purchase_remark: Mapped[str | None] = mapped_column(Text, comment="采购备注")
    cg_product_material: Mapped[str | None] = mapped_column(Text, comment="产品材质")
    is_matched_alibaba: Mapped[int | None] = mapped_column(Integer, comment="是否配对1688")
    create_time: Mapped[str | None] = mapped_column(Text, comment="创建时间(原始文本)")
    update_time: Mapped[str | None] = mapped_column(Text, comment="更新时间(原始文本)")
    unit: Mapped[str | None] = mapped_column(Text, comment="单位")
    product_developer_uid: Mapped[int | None] = mapped_column(BigInteger, comment="产品开发人UID")
    product_creator_uid: Mapped[int | None] = mapped_column(BigInteger, comment="产品创建人UID")
    cg_opt_uid: Mapped[int | None] = mapped_column(BigInteger, comment="采购负责人UID")
    attribute: Mapped[list | None] = mapped_column(JSONB, comment="属性(原始JSON)")
    ps_id: Mapped[int | None] = mapped_column(BigInteger, comment="产品系列ID")
    spu: Mapped[str | None] = mapped_column(Text, comment="SPU")
    spu_name: Mapped[str | None] = mapped_column(Text, comment="SPU名称")
    is_auto_calc: Mapped[int | None] = mapped_column(Integer, comment="是否自动计算")
    is_combined_unit: Mapped[int | None] = mapped_column(Integer, comment="是否组合单位")
    cg_package_length: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="包装长(cm)")
    cg_package_width: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="包装宽(cm)")
    cg_package_height: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="包装高(cm)")
    cg_box_length: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="外箱长(cm)")
    cg_box_width: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="外箱宽(cm)")
    cg_box_height: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="外箱高(cm)")
    cg_box_pcs: Mapped[int | None] = mapped_column(Integer, comment="单箱数量")
    cg_product_gross_weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="单件毛重(g)")
    cg_box_weight: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="外箱重量")
    cg_product_gross_weight_unit: Mapped[str | None] = mapped_column(Text, comment="单件毛重单位")
    cg_box_spec_unit: Mapped[str | None] = mapped_column(Text, comment="外箱尺寸单位")
    cg_box_weight_unit: Mapped[str | None] = mapped_column(Text, comment="外箱重量单位")
    cg_package_spec_unit: Mapped[str | None] = mapped_column(Text, comment="包装尺寸单位")
    supplier_name: Mapped[str | None] = mapped_column(Text, comment="供应商名称")
    quote_step_prices: Mapped[list | None] = mapped_column(JSONB, comment="阶梯报价(原始JSON)")
    permission_user_info: Mapped[list | None] = mapped_column(JSONB, comment="可见用户(原始JSON)")
    product_creator_realname: Mapped[str | None] = mapped_column(Text, comment="产品创建人")
    product_developer: Mapped[str | None] = mapped_column(Text, comment="产品开发人")
    cg_opt_username: Mapped[str | None] = mapped_column(Text, comment="采购负责人")
    attr: Mapped[list | None] = mapped_column(JSONB, comment="属性(原始JSON)")
    aux_num: Mapped[int | None] = mapped_column(Integer, comment="辅助数量")
    brand_name: Mapped[str | None] = mapped_column(Text, comment="品牌名称")
    status_text: Mapped[str | None] = mapped_column(Text, comment="状态文本")
    is_matched_alibaba_text: Mapped[str | None] = mapped_column(Text, comment="1688配对文本")
    category_name: Mapped[str | None] = mapped_column(Text, comment="分类名称(原始路径)")
    cg_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), comment="采购价")
    custom_fields: Mapped[list | None] = mapped_column(JSONB, comment="自定义字段(原始JSON)")
    clearance_price: Mapped[list | None] = mapped_column(JSONB, comment="清仓价(原始JSON)")
    logistics: Mapped[list | None] = mapped_column(JSONB, comment="物流信息(原始JSON)")
    sonProducts: Mapped[list | None] = mapped_column(JSONB, comment="子产品(原始JSON)")
    sonProductStr: Mapped[str | None] = mapped_column(Text, comment="子产品文本")
    comboProductStr: Mapped[str | None] = mapped_column(Text, comment="组合产品文本")
    comboProducts: Mapped[list | None] = mapped_column(JSONB, comment="组合产品(原始JSON)")
    global_tags: Mapped[list | None] = mapped_column(JSONB, comment="全局标签(原始JSON)")
    is_matched_listing: Mapped[int | None] = mapped_column(Integer, comment="是否已配对listing")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="入库时间")
