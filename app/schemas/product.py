"""产品相关 Pydantic 模型"""

from datetime import date, datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    asin: str = Field(..., max_length=20)
    product_name: str = Field(..., max_length=200)
    category: Optional[str] = None
    sub_category: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    operator: Optional[str] = None
    profit_rate: Optional[float] = None


class ProductUpdate(BaseModel):
    product_name: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    status: Optional[bool] = None
    operator: Optional[str] = None
    profit_rate: Optional[float] = None
    life_cycle: Optional[str] = None
    product_level: Optional[str] = None
    calc_frequency: Optional[str] = None
    acos_30d: Optional[float] = None


class ProductResponse(BaseModel):
    asin: str
    product_name: str
    # 身份标识
    lx_id: Optional[int] = None
    store_id: Optional[int] = None
    msku: Optional[str] = None
    local_sku: Optional[str] = None
    fnsku: Optional[str] = None
    mid: Optional[str] = None
    amz_product_id: Optional[str] = None
    amz_product_id_type_text: Optional[str] = None
    parent_asin: Optional[str] = None
    product_id: Optional[str] = None
    product_relation_id: Optional[str] = None
    id_hash: Optional[str] = None
    # 名称与描述
    listing_title: Optional[str] = None
    local_name: Optional[str] = None
    model: Optional[str] = None
    variant: Optional[str] = None
    variant_text: Optional[str] = None
    remark: Optional[str] = None
    amz_product_type: Optional[str] = None
    # 价格与费用
    price: Optional[str] = None
    listing_price: Optional[str] = None
    landed_price: Optional[str] = None
    regular_price: Optional[str] = None
    list_price: Optional[str] = None
    b2b_price: Optional[str] = None
    b2b_price_discount: Optional[str] = None
    fba_fee: Optional[str] = None
    report_fba_fee: Optional[str] = None
    referral_fee: Optional[str] = None
    shipping: Optional[str] = None
    points: Optional[str] = None
    history_price: Optional[str] = None
    history_price_source: Optional[str] = None
    currency_symbol: Optional[str] = None
    # 销量与销售额
    total_volume: Optional[int] = None
    yesterday_volume: Optional[int] = None
    seven_volume: Optional[int] = None
    fourteen_volume: Optional[int] = None
    thirty_volume: Optional[int] = None
    average_seven_volume: Optional[float] = None
    average_fourteen_volume: Optional[float] = None
    average_thirty_volume: Optional[float] = None
    yesterday_amount: Optional[float] = None
    seven_amount: Optional[float] = None
    fourteen_amount: Optional[float] = None
    thirty_amount: Optional[float] = None
    # 广告花费
    yesterday_spend: Optional[float] = None
    seven_spend: Optional[float] = None
    fourteen_spend: Optional[float] = None
    thirty_spend: Optional[float] = None
    # 库存（FBA）
    afn_fulfillable_quantity: Optional[int] = None
    afn_reserved_quantity: Optional[int] = None
    reserved_fc_transfers: Optional[int] = None
    reserved_fc_processing: Optional[int] = None
    reserved_customerorders: Optional[int] = None
    afn_inbound_shipped_quantity: Optional[int] = None
    afn_unsellable_quantity: Optional[int] = None
    afn_inbound_working_quantity: Optional[int] = None
    afn_inbound_receiving_quantity: Optional[int] = None
    quantity: Optional[int] = None
    # 排名与表现
    rank: Optional[int] = None
    seller_rank: Optional[int] = None
    category_rank: Optional[str] = None
    small_rank: Optional[str] = None
    seller_category: Optional[str] = None
    category_url: Optional[str] = None
    stars: Optional[float] = None
    reviews_num: Optional[int] = None
    # 时间字段
    open_date_time: Optional[str] = None
    first_order_time: Optional[str] = None
    first_order_type: Optional[str] = None
    first_order_update: Optional[str] = None
    on_sale_time: Optional[str] = None
    create_time: Optional[str] = None
    update_time: Optional[str] = None
    # 分类与品牌
    category_id: Optional[int] = None
    brand_id: Optional[int] = None
    brand_name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    sub_category: Optional[str] = None
    # 店铺与运营
    shop: Optional[str] = None
    marketplace: Optional[str] = None
    seller_name: Optional[str] = None
    store_type: Optional[str] = None
    fulfillment_channel_type: Optional[str] = None
    status_text: Optional[str] = None
    is_delete: Optional[str] = None
    principal_list: Optional[str] = None
    principal_uids: Optional[str] = None
    permission_user_info: Optional[str] = None
    product_creator_realname: Optional[str] = None
    product_developer: Optional[str] = None
    icon: Optional[str] = None
    # 标签与采购档案
    tags: Optional[str] = None
    supplier_name: Optional[str] = None
    cost_price: Optional[str] = None
    acos_30d: Optional[float] = None
    # 业务维护字段
    life_cycle: Optional[str] = None
    product_level: Optional[str] = None
    calc_frequency: Optional[str] = None
    product_type: Optional[str] = None
    product_stage: Optional[str] = None
    festival: Optional[str] = None
    core_months: Optional[str] = None
    lead_time: Optional[int] = None
    box_quantity: Optional[int] = None
    min_order_qty: Optional[int] = None
    list_date: Optional[date] = None
    status: bool
    operator: Optional[str] = None
    primary_operator: Optional[str] = None
    is_new: Optional[bool] = None
    profit_rate: Optional[float] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProductPage(BaseModel):
    """产品分页响应"""

    total: int
    items: List[ProductResponse]
