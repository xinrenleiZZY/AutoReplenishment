"""库存快照表"""

from datetime import date, datetime
from sqlalchemy import String, Integer, Date, DateTime, BigInteger, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import ForeignKey

from app.database import Base


class InventorySnapshot(Base):
    __tablename__ = "inventory_snapshots"
    __table_args__ = (
        UniqueConstraint("asin", "snapshot_date", name="uq_asin_snapshot_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    asin: Mapped[str] = mapped_column(String(20), ForeignKey("products.asin"), index=True, comment="ASIN编码")
    snapshot_date: Mapped[date] = mapped_column(Date, comment="快照日期")
    fba_available: Mapped[int] = mapped_column(Integer, default=0, comment="FBA可售库存")
    fba_reserved: Mapped[int] = mapped_column(Integer, default=0, comment="FBA预留库存")
    fba_inbound: Mapped[int] = mapped_column(Integer, default=0, comment="FBA在途/入库中")
    fba_inbound_shipped: Mapped[int] = mapped_column(Integer, default=0, comment="FBA在途已发货")
    local_stock: Mapped[int] = mapped_column(Integer, default=0, comment="本地仓库存")
    purchase_on_order: Mapped[int] = mapped_column(Integer, default=0, comment="采购单待到货量")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
