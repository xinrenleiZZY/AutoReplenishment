"""后台任务持久化表（Phase 3 / B-04）

Revision ID: 0005_task_jobs
Revises: 0004_config_audit
Create Date: 2026-10-10

对应整改方案 B-04：把 `_jobs` / `_single_tasks` 内存登记表落库。
全部使用 IF NOT EXISTS，可在既有库上重复执行。
"""

from alembic import op

revision = "0005_task_jobs"
down_revision = "0004_config_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_jobs (
            id BIGSERIAL PRIMARY KEY,
            job_id VARCHAR(32) NOT NULL UNIQUE,
            kind VARCHAR(40),
            status VARCHAR(12) NOT NULL DEFAULT 'running',
            operator VARCHAR(60),
            params_json TEXT,
            result_json TEXT,
            progress_json TEXT,
            error_message TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT now(),
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_task_jobs_job_id ON task_jobs (job_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_task_jobs_kind ON task_jobs (kind)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_task_jobs_status ON task_jobs (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_task_jobs_created_at ON task_jobs (created_at)")


def downgrade() -> None:
    for idx in ("ix_task_jobs_job_id", "ix_task_jobs_kind", "ix_task_jobs_status", "ix_task_jobs_created_at"):
        op.execute(f"DROP INDEX IF EXISTS {idx}")
    op.execute("DROP TABLE IF EXISTS task_jobs")
