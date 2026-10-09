"""sync_logs 可观测性扩展（run_id/level/step/stats_json/duration_ms/retry_of）

Revision ID: 0002_sync_log_observability
Revises: 0001_baseline
Create Date: 2026-10-08

对应整改方案 G-16：让 sync_logs 能表达「步骤级成败、部分成功、耗时、重试关系」。
全部使用 IF NOT EXISTS，可在既有库上重复执行。
"""

from alembic import op

revision = "0002_sync_log_observability"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS run_id VARCHAR(40)")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS level VARCHAR(10) DEFAULT 'task'")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS step VARCHAR(40)")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS parent_id BIGINT")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS source VARCHAR(40)")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS stats_json TEXT")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS duration_ms INTEGER")
    op.execute("ALTER TABLE sync_logs ADD COLUMN IF NOT EXISTS retry_of BIGINT")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sync_logs_run_id ON sync_logs (run_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_sync_logs_started_at ON sync_logs (started_at)")
    # status 由 VARCHAR(10) 放宽到 VARCHAR(12)，避免 partial/interrupted 被截断
    op.execute("ALTER TABLE sync_logs ALTER COLUMN status TYPE VARCHAR(12)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_sync_logs_run_id")
    op.execute("DROP INDEX IF EXISTS ix_sync_logs_started_at")
    for col in ("run_id", "level", "step", "parent_id", "source",
                "stats_json", "duration_ms", "retry_of"):
        op.execute(f"ALTER TABLE sync_logs DROP COLUMN IF EXISTS {col}")
