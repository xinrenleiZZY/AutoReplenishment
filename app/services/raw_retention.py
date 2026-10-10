"""原始响应与抓取产物的保留/归档（Phase 3 / B-07）

现状问题：`api_raw_responses` 全量入库、无 TTL；`p_id/`（sales_history、sellable_gap、
sif_lifecycle 等）持续增长，磁盘占用不可预期。

本模块提供可审计、可演练的治理动作：
  · stats()：看当前体量（行数、最老/最新抓取日期、表占用字节）
  · archive_and_purge()：把超过保留期的行**先归档成 .jsonl.gz 再分批删除**
  · 默认 dry_run=True（只统计不动数据）；真正执行由脚本显式加 --yes/--archive 触发

注意：不做"静默自动清理"——保留天数由参数表 raw_retention_days 控制，
是否执行由运维通过脚本决定（也可后续接到调度任务上）。
"""

import gzip
import json
import logging
import os
from datetime import date, timedelta

from sqlalchemy import delete, func, select, text

from app.database import async_session_factory
from app.models.api_raw import ApiRawResponse

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ARCHIVE_DIR = os.path.join(ROOT, "_archive")
DEFAULT_RETENTION_DAYS = 90
BATCH = 2000

_FIELDS = ("id", "source", "asin", "request_url", "request_method", "request_params",
           "response_raw", "status_code", "error", "fetch_date", "created_at")


async def stats() -> dict:
    """当前体量（用于"增长曲线可预期"的观测）。"""
    async with async_session_factory() as s:
        rows = (await s.execute(select(func.count()).select_from(ApiRawResponse))).scalar() or 0
        oldest = (await s.execute(select(func.min(ApiRawResponse.fetch_date)))).scalar()
        newest = (await s.execute(select(func.max(ApiRawResponse.fetch_date)))).scalar()
        size = (await s.execute(text("select pg_total_relation_size('api_raw_responses')"))).scalar()
    return {
        "rows": int(rows),
        "oldest_fetch_date": oldest.isoformat() if oldest else None,
        "newest_fetch_date": newest.isoformat() if newest else None,
        "table_bytes": int(size or 0),
        "table_mb": round(int(size or 0) / 1024 / 1024, 1),
    }


async def archive_and_purge(days: int = DEFAULT_RETENTION_DAYS,
                            archive_dir: str | None = None,
                            dry_run: bool = True,
                            batch: int = BATCH) -> dict:
    """删除（并可选归档）`fetch_date < today - days` 的原始响应行。

    archive_dir 非空 → 先写 .jsonl.gz 再删除（归档成功才删）；为空 → 直接删。
    返回 stats 结构，供脚本/日志留痕。
    """
    cutoff = date.today() - timedelta(days=max(0, int(days)))
    out = {"cutoff": cutoff.isoformat(), "days": days, "dry_run": dry_run,
           "candidate_rows": 0, "archived_rows": 0, "deleted_rows": 0, "archive_file": None}
    async with async_session_factory() as s:
        out["candidate_rows"] = int((await s.execute(
            select(func.count()).select_from(ApiRawResponse).where(ApiRawResponse.fetch_date < cutoff)
        )).scalar() or 0)
        if dry_run or out["candidate_rows"] == 0:
            return out

        if archive_dir:
            os.makedirs(archive_dir, exist_ok=True)
            path = os.path.join(archive_dir, f"api_raw_responses_{cutoff.isoformat()}.jsonl.gz")
            written = 0
            with gzip.open(path, "wt", encoding="utf-8") as f:
                while True:
                    rows = (await s.execute(
                        select(ApiRawResponse).where(ApiRawResponse.fetch_date < cutoff)
                        .order_by(ApiRawResponse.id).limit(batch)
                    )).scalars().all()
                    if not rows:
                        break
                    for r in rows:
                        f.write(json.dumps({k: getattr(r, k) for k in _FIELDS},
                                           ensure_ascii=False, default=str) + "\n")
                    ids = [r.id for r in rows]
                    await s.execute(delete(ApiRawResponse).where(ApiRawResponse.id.in_(ids)))
                    await s.commit()
                    written += len(rows)
            out["archive_file"] = path
            out["archived_rows"] = written
            out["deleted_rows"] = written
        else:
            res = await s.execute(delete(ApiRawResponse).where(ApiRawResponse.fetch_date < cutoff))
            await s.commit()
            out["deleted_rows"] = res.rowcount or 0
    logger.info("api_raw_responses 治理完成: %s", out)
    return out
