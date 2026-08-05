"""每日销量快照表 — 每日一次抓取入库，作为统一数据源"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Float, Date, DateTime, BigInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.database import Base


class DailySalesSnapshot(Base):
    __tablename__ = "daily_sales_snapshots"
    __table_args__ = (
        UniqueConstraint("asin", "snapshot_date", name="uq_snapshot_asin_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    snapshot_date: Mapped[date] = mapped_column(Date, index=True, comment="快照日期")

    # 销量指标（来自领星 showOnline / productExpressionNew）
    yesterday_volume: Mapped[int | None] = mapped_column(Integer, comment="昨日销量")
    seven_volume: Mapped[int | None] = mapped_column(Integer, comment="近7天销量")
    fourteen_volume: Mapped[int | None] = mapped_column(Integer, comment="近14天销量")
    thirty_volume: Mapped[int | None] = mapped_column(Integer, comment="近30天销量")
    total_volume: Mapped[int | None] = mapped_column(Integer, comment="历史总销量")
    average_seven_volume: Mapped[float | None] = mapped_column(Float, comment="近7天日均销量")
    average_fourteen_volume: Mapped[float | None] = mapped_column(Float, comment="近14天日均销量")
    average_thirty_volume: Mapped[float | None] = mapped_column(Float, comment="近30天日均销量")

    # 销售额/广告
    yesterday_amount: Mapped[float | None] = mapped_column(Float, comment="昨日销售额")
    seven_amount: Mapped[float | None] = mapped_column(Float, comment="近7天销售额")
    thirty_amount: Mapped[float | None] = mapped_column(Float, comment="近30天销售额")
    thirty_spend: Mapped[float | None] = mapped_column(Float, comment="近30天广告花费")
    seven_spend: Mapped[float | None] = mapped_column(Float, comment="近7天广告花费")

    # 库存/排名
    afn_fulfillable_quantity: Mapped[int | None] = mapped_column(Integer, comment="可售库存")
    afn_reserved_quantity: Mapped[int | None] = mapped_column(Integer, comment="预留库存")
    afn_inbound_shipped_quantity: Mapped[int | None] = mapped_column(Integer, comment="在途库存")
    category_rank: Mapped[int | None] = mapped_column(Integer, comment="类目排名")

    # SIF 补充信号（每日拉取，写入同快照）
    sif_monthly_sales: Mapped[str | None] = mapped_column(String, comment="SIF月度销量JSON")
    sif_growth_rate: Mapped[float | None] = mapped_column(Float, comment="SIF销量增长率")
    sif_ad_spend_change: Mapped[float | None] = mapped_column(Float, comment="SIF广告投入变化率")
    sif_cvr_change: Mapped[float | None] = mapped_column(Float, comment="SIF转化率变化率")
    sif_rating: Mapped[float | None] = mapped_column(Float, comment="SIF评分")
    sif_review_count: Mapped[int | None] = mapped_column(Integer, comment="SIF评论数")
    sif_review_growth: Mapped[int | None] = mapped_column(Integer, comment="SIF评论增长量")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
