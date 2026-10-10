"""参数变更审计表（Phase 1 / G-12）

Revision ID: 0004_config_audit
Revises: 0003_purchase_order_tables
Create Date: 2026-10-10

用途：回答"谁把日报范围改成 3 个生命周期"——config_params 的每次写入都留痕。
全部使用 IF NOT EXISTS，可在既有库上重复执行。
"""

from alembic import op

revision = "0004_config_audit"
down_revision = "0003_purchase_order_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS config_audit_logs (
            id BIGSERIAL PRIMARY KEY,
            param_key VARCHAR(60) NOT NULL,
            old_value TEXT,
            new_value TEXT,
            operator VARCHAR(60),
            source VARCHAR(30),
            changed_at TIMESTAMP NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_config_audit_logs_param_key ON config_audit_logs (param_key)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_config_audit_logs_changed_at ON config_audit_logs (changed_at)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_config_audit_logs_param_key")
    op.execute("DROP INDEX IF EXISTS ix_config_audit_logs_changed_at")
    op.execute("DROP TABLE IF EXISTS config_audit_logs")
