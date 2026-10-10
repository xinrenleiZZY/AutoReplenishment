"""后台任务持久化读写（Phase 3 / B-04）

设计：内存字典（`_jobs` / `_single_tasks`）继续作为**实时进度**的唯一来源，
数据库只做**留痕与恢复**：
  · 任务创建/状态变化时写一行 task_jobs（best-effort，失败只告警不影响主流程）；
  · 查询时内存优先，内存没有（例如 API 重启过）则回落到数据库；
  · 启动时把超时 running 的任务标记为 interrupted；
  · 启动时返回"未到期的定时任务"清单，供调用方重新注册调度。
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import desc, or_, select, update

from app.database import async_session_factory
from app.models.task_job import TaskJob

logger = logging.getLogger(__name__)


def _dumps(v) -> str | None:
    if v is None:
        return None
    try:
        return json.dumps(v, ensure_ascii=False, default=str)[:60000]
    except Exception:  # noqa: BLE001
        return str(v)[:60000]


def _loads(s: str | None):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return None


def _epoch(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt else None


async def save_calc_job(job: dict) -> None:
    """把 `_jobs[job_id]` 的当前状态落库（不存在则插入）。"""
    job_id = job.get("job_id")
    if not job_id:
        return
    try:
        async with async_session_factory() as s:
            row = (await s.execute(select(TaskJob).where(TaskJob.job_id == job_id))).scalar_one_or_none()
            now = datetime.now()
            if row is None:
                row = TaskJob(job_id=job_id, kind=job.get("kind"),
                              status=job.get("status") or "running",
                              created_at=now, started_at=now)
                s.add(row)
            row.status = job.get("status") or row.status
            row.kind = job.get("kind") or row.kind
            row.result_json = _dumps(job.get("stats"))
            row.progress_json = _dumps(job.get("progress"))
            row.error_message = (str(job.get("error"))[:2000] if job.get("error") else None)
            if job.get("finished_at"):
                row.completed_at = datetime.fromtimestamp(job["finished_at"])
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("任务持久化失败 job_id=%s: %s", job_id, e)


def save_calc_job_bg(job: dict) -> None:
    """fire-and-forget 版本（供同步代码路径调用）"""
    try:
        asyncio.create_task(save_calc_job(job))
    except RuntimeError:  # 无事件循环（CLI/测试）
        pass


async def save_task_hall_single(task: dict) -> None:
    """把任务大厅-单ASIN任务状态落库（job_id 用 task_id）。"""
    job_id = task.get("task_id") or task.get("id")
    if not job_id:
        return
    try:
        async with async_session_factory() as s:
            row = (await s.execute(select(TaskJob).where(TaskJob.job_id == job_id))).scalar_one_or_none()
            now = datetime.now()
            if row is None:
                row = TaskJob(job_id=job_id, kind="task-hall-single",
                              status=task.get("status") or "scheduled", created_at=now)
                s.add(row)
            row.status = task.get("status") or row.status
            row.params_json = _dumps({
                "asin": task.get("asin"),
                "run_at": task.get("run_at"),
                "with_report": task.get("with_report"),
                "with_image": task.get("with_image"),
            })
            row.operator = task.get("operator") or row.operator
            row.result_json = _dumps(task.get("stats"))
            row.error_message = (str(task.get("error"))[:2000] if task.get("error") else None)
            for key, col in (("started_at", "started_at"), ("finished_at", "completed_at")):
                val = task.get(key)
                if val:
                    try:
                        setattr(row, col, datetime.fromisoformat(str(val)))
                    except ValueError:
                        pass
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("任务大厅任务持久化失败 task_id=%s: %s", job_id, e)


def save_task_hall_single_bg(task: dict) -> None:
    try:
        asyncio.create_task(save_task_hall_single(task))
    except RuntimeError:
        pass


async def mark_cancelled(job_id: str) -> None:
    """把任务标记为 cancelled（取消/删除时调用）。"""
    if not job_id:
        return
    try:
        async with async_session_factory() as s:
            await s.execute(
                update(TaskJob).where(TaskJob.job_id == job_id, TaskJob.status.in_(("scheduled", "running", "pending")))
                .values(status="cancelled", completed_at=datetime.now(), error_message="用户取消/删除")
            )
            await s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("任务取消留痕失败 job_id=%s: %s", job_id, e)


async def fetch_job(job_id: str) -> dict | None:
    """按 job_id 读取任务（内存没有时的回落路径），返回与内存字典兼容的结构。"""
    async with async_session_factory() as s:
        row = (await s.execute(select(TaskJob).where(TaskJob.job_id == job_id))).scalar_one_or_none()
    if row is None:
        return None
    return {
        "job_id": row.job_id,
        "kind": row.kind,
        "status": row.status,
        "started_at": _epoch(row.started_at),
        "finished_at": _epoch(row.completed_at),
        "stats": _loads(row.result_json),
        "progress": _loads(row.progress_json),
        "error": row.error_message,
        "from_db": True,
    }


async def list_jobs(kind: str | None = None, limit: int = 50) -> list[dict]:
    """任务历史（按创建时间倒序），供"重启后仍能看到历史"的场景使用。"""
    q = select(TaskJob)
    if kind:
        q = q.where(TaskJob.kind == kind)
    async with async_session_factory() as s:
        rows = (await s.execute(
            q.order_by(desc(TaskJob.created_at)).limit(max(1, min(limit, 200)))
        )).scalars().all()
    return [
        {
            "job_id": r.job_id, "kind": r.kind, "status": r.status, "operator": r.operator,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "error": r.error_message, "params": _loads(r.params_json),
        }
        for r in rows
    ]
async def mark_orphan_running_interrupted(*, exclude_kinds: tuple[str, ...] | None = None,
                                          only_kinds: tuple[str, ...] | None = None) -> int:
    """启动时把"上次进程遗留的 running 任务"标记为 interrupted。

    当前是单 worker 部署：进程一重启，内存里的执行协程就没了，DB 里任何
    running 行都必然是孤儿行。等 6 小时巡检太慢（页面会一直显示"运行中"），
    所以在启动时立即判定。若将来改成多副本，需要改为按 owner/heartbeat 判定。

    Phase 3 / B-03：任务大厅单ASIN任务由**独立调度服务**执行，因此
      · API 进程只清 kind != task-hall-single 的孤儿（exclude_kinds）；
      · 调度服务只清 kind == task-hall-single 的孤儿（only_kinds）。
    """
    try:
        cond = [TaskJob.status == "running"]
        if only_kinds:
            cond.append(TaskJob.kind.in_(only_kinds))
        if exclude_kinds:
            cond.append(or_(TaskJob.kind.is_(None), TaskJob.kind.notin_(exclude_kinds)))
        async with async_session_factory() as s:
            res = await s.execute(
                update(TaskJob)
                .where(*cond)
                .values(status="interrupted", completed_at=datetime.now(),
                        error_message="进程重启：任务已中断，可重新触发")
            )
            await s.commit()
            n = res.rowcount or 0
        if n:
            logger.warning("启动巡检：%d 条 running 后台任务标记为 interrupted（进程重启）", n)
        return n
    except Exception as e:  # noqa: BLE001
        logger.error("后台任务孤儿巡检失败: %s", e)
        return 0


async def mark_stale_running_interrupted(max_hours: int = 6) -> int:
    """把超过 max_hours 仍未结束的 running 任务标记为 interrupted（进程被杀/重启）"""
    cutoff = datetime.now() - timedelta(hours=max_hours)
    try:
        async with async_session_factory() as s:
            res = await s.execute(
                update(TaskJob)
                .where(TaskJob.status.in_(("running", "scheduled")), TaskJob.created_at < cutoff)
                .values(status="interrupted", completed_at=datetime.now(),
                        error_message="进程中断：任务超过 %d 小时无终态，已由启动巡检标记" % max_hours)
            )
            await s.commit()
            n = res.rowcount or 0
        if n:
            logger.warning("已标记 %d 条超时的后台任务为 interrupted", n)
        return n
    except Exception as e:  # noqa: BLE001
        logger.error("后台任务启动巡检失败: %s", e)
        return 0


async def pending_scheduled_singles() -> list[dict]:
    """启动时取回"尚未执行的定时单ASIN任务"，供调用方重新注册调度。"""
    try:
        async with async_session_factory() as s:
            rows = (await s.execute(
                select(TaskJob).where(TaskJob.kind == "task-hall-single",
                                      TaskJob.status.in_(("scheduled", "pending")))
            )).scalars().all()
    except Exception as e:  # noqa: BLE001
        logger.warning("读取待执行定时任务失败: %s", e)
        return []
    out = []
    for r in rows:
        params = _loads(r.params_json) or {}
        out.append({"task_id": r.job_id, "asin": params.get("asin"), "run_at": params.get("run_at"),
                    "with_report": params.get("with_report"), "with_image": params.get("with_image"),
                    "operator": r.operator})
    return out
