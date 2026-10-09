"""baseline: 以当前模型定义作为版本化迁移的起点

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-08

说明（Phase 1 / G-08）：
  既有生产库早就存在（靠 app.database.init_db 的 create_all 建表），因此本 baseline：
    - 对**空库**：按当前模型建全部表；
    - 对**既有库**：不要在 upgrade 时执行，改用 `alembic stamp 0001_baseline` 打标，
      之后所有结构变更一律走新迁移（0002 起）。
"""

from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401  确保所有模型已注册到 metadata

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    from app.database import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
