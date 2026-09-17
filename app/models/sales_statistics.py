"""销售统计报表表 — 领星 sales-statistics/report/list 网页API 抓取落库

按 (asin, 统计区间) 幂等：同一 ASIN 同一区间只保留最新一次抓取。
嵌套/数组字段以 JSON 字符串存储，另存 raw_data 完整原始数据兜底（不丢任何字段）。
"""

from datetime import date, datetime
from sqlalchemy import String, Integer, BigInteger, Date, DateTime, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SalesStatisticsReport(Base):
    __tablename__ = "sales_statistics_reports"
    __table_args__ = (
        UniqueConstraint("asin", "stat_start_date", "stat_end_date",
                         name="uq_sales_stat_asin_range"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), index=True, comment="ASIN编码")
    stat_start_date: Mapped[str] = mapped_column(String(10), comment="统计区间开始(YYYY-MM-DD)")
    stat_end_date: Mapped[str] = mapped_column(String(10), comment="统计区间结束(YYYY-MM-DD)")
    query_type: Mapped[str | None] = mapped_column(String(20), comment="查询指标类型(volume/amount等)")
    group_type: Mapped[str | None] = mapped_column(String(20), comment="分组类型(asin)")
    fetch_date: Mapped[date] = mapped_column(Date, index=True, comment="抓取日期")

    # ── 标量字段（提取便于直接查询） ──
    icon: Mapped[str | None] = mapped_column(String(10), comment="货币符号")
    product_name: Mapped[str | None] = mapped_column(Text, comment="产品名称")
    rel_key: Mapped[str | None] = mapped_column(Text, comment="rel_key")
    currency_code: Mapped[str | None] = mapped_column(String(10), comment="币种")
    pic_url: Mapped[str | None] = mapped_column(Text, comment="产品图片URL")
    ps_id: Mapped[str | None] = mapped_column(String(30), comment="ps_id")
    spu: Mapped[str | None] = mapped_column(String(100), comment="SPU")
    spu_name: Mapped[str | None] = mapped_column(String(200), comment="SPU名称")
    developer: Mapped[str | None] = mapped_column(String(100), comment="开发人")
    seller_principal_usernames: Mapped[str | None] = mapped_column(Text, comment="负责人用户名(逗号分隔)")
    store_type: Mapped[str | None] = mapped_column(String(50), comment="店铺类型")
    total_value: Mapped[str | None] = mapped_column(String(50), comment="total.value(查询指标汇总)")
    total_survey_value: Mapped[str | None] = mapped_column(String(50), comment="total.survey_value")
    avg_total_value: Mapped[str | None] = mapped_column(String(50), comment="avg_total.value(均值)")
    avg_total_survey_value: Mapped[str | None] = mapped_column(String(50), comment="avg_total.survey_value")

    # ── JSON 字段（嵌套/数组 → JSON字符串，全部字段落库） ──
    sid_json: Mapped[str | None] = mapped_column(Text, comment="sid 列表JSON")
    asin_json: Mapped[str | None] = mapped_column(Text, comment="asin 列表JSON")
    bid_json: Mapped[str | None] = mapped_column(Text, comment="bid 列表JSON")
    cid_json: Mapped[str | None] = mapped_column(Text, comment="cid 列表JSON")
    marketplace_json: Mapped[str | None] = mapped_column(Text, comment="站点列表JSON")
    inventory_json: Mapped[str | None] = mapped_column(Text, comment="库存明细JSON")
    model_json: Mapped[str | None] = mapped_column(Text, comment="型号列表JSON")
    seller_name_json: Mapped[str | None] = mapped_column(Text, comment="卖家名称列表JSON")
    trend_data_json: Mapped[str | None] = mapped_column(Text, comment="趋势数据JSON(年度/月度)")
    category_text_json: Mapped[str | None] = mapped_column(Text, comment="类目列表JSON")
    product_brand_text_json: Mapped[str | None] = mapped_column(Text, comment="品牌列表JSON")
    parent_asin_json: Mapped[str | None] = mapped_column(Text, comment="父ASIN列表JSON")
    local_name_json: Mapped[str | None] = mapped_column(Text, comment="内部品名列表JSON")
    local_sku_json: Mapped[str | None] = mapped_column(Text, comment="本地SKU列表JSON")
    principal_name_json: Mapped[str | None] = mapped_column(Text, comment="负责人列表JSON")
    local_info_json: Mapped[str | None] = mapped_column(Text, comment="local_info列表JSON")
    global_tags_json: Mapped[str | None] = mapped_column(Text, comment="全局标签列表JSON")
    sid_mskus_json: Mapped[str | None] = mapped_column(Text, comment="sid_mskus列表JSON")
    seller_skus_json: Mapped[str | None] = mapped_column(Text, comment="seller_skus列表JSON")
    seller_info_json: Mapped[str | None] = mapped_column(Text, comment="seller_info列表JSON")
    fnsku_json: Mapped[str | None] = mapped_column(Text, comment="FNSKU列表JSON")
    attribute_json: Mapped[str | None] = mapped_column(Text, comment="attribute JSON")

    # ── 原始完整数据（兜底，保证不丢字段） ──
    raw_data: Mapped[str | None] = mapped_column(Text, comment="单条原始响应JSON")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
