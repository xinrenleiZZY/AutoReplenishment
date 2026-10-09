"""采购单产品明细表纳入版本管理（purchase_order / purchase_order_item）

Revision ID: 0003_purchase_order_tables
Revises: 0002_sync_log_observability
Create Date: 2026-10-09

背景：这两张表此前是靠 app.database.init_db() 的 create_all 在启动时隐式创建的，
      没有对应迁移 → 用迁移在空库建库会缺表（schema 漂移）。本迁移补上：
        · 既有库：表已存在，create_all(tables=[...]) 幂等跳过，不重建、不动数据；
        · 空库：按当前模型定义建这两张表。
"""

from alembic import op

revision = "0003_purchase_order_tables"
down_revision = "0002_sync_log_observability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401  注册全部模型
    from app.models.purchase_order import PurchaseOrder
    from app.models.purchase_order_item import PurchaseOrderItem

    Base.metadata.create_all(
        bind=op.get_bind(),
        tables=[PurchaseOrder.__table__, PurchaseOrderItem.__table__],
    )


def downgrade() -> None:
    from app.models.purchase_order import PurchaseOrder
    from app.models.purchase_order_item import PurchaseOrderItem

    bind = op.get_bind()
    PurchaseOrderItem.__table__.drop(bind=bind, checkfirst=True)
    PurchaseOrder.__table__.drop(bind=bind, checkfirst=True)
