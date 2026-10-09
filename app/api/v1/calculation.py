"""计算任务 API 路由"""

import json
import logging
import time
import uuid
import asyncio
from datetime import date, datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory, get_session
from app.models.calculation import (
    CalculationResult,
    CalculationStepResult,
    CalculationTimelineReset,
    CalculationSkipLog,
)
from app.models.product import Product
from app.models.operator import Operator
from app.models.ai_evaluation import AiEvaluation
from app.schemas.calculation import CalculationResultResponse, FeedbackRequest, AdoptRequest, TimelineResetRequest
from app.tasks.calculation_tasks import (
    run_single_calculation,
    run_batch_calculation,
    run_due_calculation,
    run_level_calculation,
    get_due_calculation_stats,
    get_next_calculation_info,
    get_daily_summary,
    _latest_calculation_rows,
    reset_calculation_timeline,
    normalize_level,
    _normalize_levels,
    _load_report_lifecycles,
)
from app.integrations.feishu import FeishuNotifier
from app.services import config_service
from app.services import ai_eval

router = APIRouter()
logger = logging.getLogger(__name__)

# 后台计算任务（内存登记，单 worker 场景够用；重启后任务丢失可重新触发）
_jobs: dict[str, dict] = {}


def _start_job(run_factory, progress: dict | None = None, kind: str | None = None) -> str:
    """提交后台任务并返回 job_id；kind 用于区分任务来源，便于按来源查询/恢复"""
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {"job_id": job_id, "kind": kind, "status": "running", "started_at": time.time(),
                     "finished_at": None, "stats": None, "error": None,
                     "progress": progress or {"total": 0, "done": 0, "percent": 0, "current_asin": None}}

    async def _runner():
        try:
            stats = await run_factory()
            _jobs[job_id]["stats"] = stats
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["progress"]["percent"] = 100
            _jobs[job_id]["progress"]["done"] = _jobs[job_id]["progress"]["total"]
            _jobs[job_id]["progress"]["current_asin"] = None
        except Exception as e:  # noqa: BLE001
            logger.error("后台任务 %s 失败: %s", job_id, e)
            _jobs[job_id]["status"] = "failed"
            _jobs[job_id]["error"] = str(e)
        finally:
            _jobs[job_id]["finished_at"] = time.time()

    asyncio.create_task(_runner())
    return job_id


def _parse_json(s: str | None):
    """JSON字符串转对象"""
    if not s:
        return None
    import json
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


def _to_float(val, default=None):
    """安全转 float"""
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


@router.get("/results", response_model=List[CalculationResultResponse])
async def list_results(
    asin: Optional[str] = Query(None),
    purchase_level: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """获取计算结果列表"""
    query = select(CalculationResult)
    if asin:
        query = query.where(CalculationResult.asin == asin)
    if purchase_level:
        query = query.where(CalculationResult.purchase_level == purchase_level)
    query = query.order_by(CalculationResult.calc_date.desc()).offset(skip).limit(limit)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/results/overview")
async def list_results_overview(
    keyword: Optional[str] = Query(None, description="按 ASIN/产品名称/分类搜索"),
    operator: Optional[str] = Query(None, description="按负责人筛选（多个负责人时包含即命中）"),
    product_level: Optional[str] = Query(None, description="按产品等级筛选（S/A/B/C/D）"),
    life_cycle: Optional[str] = Query(None, description="按生命周期筛选"),
    purchase_level: Optional[str] = Query(None),
    calc_date: Optional[str] = Query(None, description="指定查询日期（YYYY-MM-DD，单日），不传则取最近一次结果"),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """全部产品 + 计算结果（不传 calc_date 取最近一次；传 calc_date 查询该单日所有计算结果）"""
    from sqlalchemy import and_, or_

    query_date: date | None = None
    if calc_date:
        try:
            query_date = date.fromisoformat(calc_date.strip())
        except ValueError:
            raise HTTPException(status_code=400, detail="calc_date 格式错误，应为 YYYY-MM-DD")

    if query_date is not None:
        latest_sub = (
            select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
            .where(CalculationResult.calc_date == query_date)
            .group_by(CalculationResult.asin)
            .subquery()
        )
    else:
        latest_sub = (
            select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
            .group_by(CalculationResult.asin)
            .subquery()
        )
    latest_join = (
        select(CalculationResult)
        .join(
            latest_sub,
            and_(CalculationResult.asin == latest_sub.c.asin, CalculationResult.calc_date == latest_sub.c.d),
        )
        .subquery()
    )

    base = select(Product).where(Product.status == True)  # noqa: E712
    if keyword:
        from sqlalchemy import or_ as _or

        like = f"%{keyword.strip()}%"
        base = base.where(_or(
            Product.asin.ilike(like),
            Product.product_name.ilike(like),
            Product.category.ilike(like),
        ))
    if operator:
        like = f"%{operator.strip()}%"
        base = base.where(Product.primary_operator.ilike(like))
    if product_level:
        base = base.where(Product.product_level == product_level.strip().upper())
    if life_cycle:
        base = base.where(Product.life_cycle == life_cycle.strip())
    if purchase_level:
        base = base.where(
            Product.asin.in_(
                select(latest_join.c.asin).where(latest_join.c.purchase_level == purchase_level)
            )
        )
    # 指定日期时仅展示该日有计算结果的 ASIN
    if query_date is not None:
        base = base.where(
            Product.asin.in_(
                select(CalculationResult.asin).where(CalculationResult.calc_date == query_date)
            )
        )

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await session.execute(
        base.order_by(Product.asin).offset(skip).limit(limit)
    )).scalars().all()

    asins = [p.asin for p in rows]
    latest: dict[str, dict] = {}
    if asins:
        q = (
            select(
                latest_join.c.asin,
                latest_join.c.calc_date,
                latest_join.c.purchase_score,
                latest_join.c.purchase_level,
                latest_join.c.suggested_qty,
                latest_join.c.inventory_days,
                latest_join.c.replenishment_cycle,
                latest_join.c.available_stock,
                latest_join.c.urgency_score,
                latest_join.c.purchase_trigger,
                latest_join.c.user_feedback,
                latest_join.c.feedback_at,
                latest_join.c.adopted,
                latest_join.c.adopted_at,
                latest_join.c.adopted_by,
                latest_join.c.adopted_confidence,
                latest_join.c.base_score,
                latest_join.c.product_stage,
                latest_join.c.product_level,
                latest_join.c.life_cycle,
            )
            .where(latest_join.c.asin.in_(asins))
        )
        for row in (await session.execute(q)).all():
            latest[row[0]] = {
                "calc_date": row[1].isoformat() if row[1] else None,
                "purchase_score": row[2],
                "purchase_level": row[3],
                "suggested_qty": row[4],
                "inventory_days": row[5],
                "replenishment_cycle": row[6],
                "available_stock": row[7],
                "urgency_score": row[8],
                "purchase_trigger": row[9],
                "user_feedback": row[10],
                "feedback_at": row[11].isoformat() if row[11] else None,
                "adopted": row[12],
                "adopted_at": row[13].isoformat() if row[13] else None,
                "adopted_by": row[14],
                "adopted_confidence": row[15],
                "base_score": row[16],
                "product_stage": row[17],
                "product_level": row[18],
                "life_cycle": row[19],
            }

    items = []
    for p in rows:
        r = latest.get(p.asin)
        items.append({
            "asin": p.asin,
            "product_name": p.product_name,
            "operator": p.operator,
            "primary_operator": p.primary_operator,
            "product_level": p.product_level,
            "life_cycle": p.life_cycle,
            "product_stage": p.product_stage,
            "product_type": p.product_type,
            "calc_date": r["calc_date"] if r else None,
            "purchase_score": r["purchase_score"] if r else None,
            "base_score": r["base_score"] if r else None,
            "product_stage": r["product_stage"] if r else None,
            "product_level": r["product_level"] if r else None,
            "life_cycle": r["life_cycle"] if r else None,
            "purchase_level": normalize_level(r["purchase_level"]) if r else None,
            "suggested_qty": r["suggested_qty"] if r else None,
            "inventory_days": r["inventory_days"] if r else None,
            "replenishment_cycle": r["replenishment_cycle"] if r else None,
            "available_stock": r["available_stock"] if r else None,
            "urgency_score": r["urgency_score"] if r else None,
            "purchase_trigger": r["purchase_trigger"] if r else None,
            "user_feedback": r["user_feedback"] if r else None,
            "feedback_at": r["feedback_at"] if r else None,
            "adopted": r["adopted"] if r else None,
            "adopted_at": r["adopted_at"] if r else None,
            "adopted_by": r["adopted_by"] if r else None,
            "adopted_confidence": r["adopted_confidence"] if r else None,
        })

    return {"total": total, "items": items}


@router.get("/inventory-health")
async def list_inventory_health(
    keyword: Optional[str] = Query(None, description="按 ASIN/品名/分类搜索"),
    operator: Optional[str] = Query(None, description="按负责人筛选（多个负责人时包含即命中）"),
    urgency: Optional[str] = Query(None, description="紧急程度：危险/偏低/健康/过量"),
    purchase_level: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """库存健康分析：全部字段 + 品名/负责人 + 紧急程度分档（阈值可在库存页/参数页调整）"""
    from sqlalchemy import and_, or_

    danger = await config_service.get_param(session, "inventory_danger_max_days") or 15
    low = await config_service.get_param(session, "inventory_low_max_days") or 30
    healthy = await config_service.get_param(session, "inventory_healthy_max_days") or 90

    def classify(days: int | None) -> str | None:
        if days is None:
            return None
        if days < danger:
            return "危险"
        if days < low:
            return "偏低"
        if days <= healthy:
            return "健康"
        return "过量"

    latest_sub = (
        select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
        .group_by(CalculationResult.asin)
        .subquery()
    )
    latest_join = (
        select(CalculationResult)
        .join(
            latest_sub,
            and_(CalculationResult.asin == latest_sub.c.asin, CalculationResult.calc_date == latest_sub.c.d),
        )
        .subquery()
    )

    base = select(Product).where(Product.status == True)  # noqa: E712
    if keyword:
        like = f"%{keyword.strip()}%"
        base = base.where(or_(
            Product.asin.ilike(like),
            Product.product_name.ilike(like),
            Product.category.ilike(like),
        ))
    if operator:
        like = f"%{operator.strip()}%"
        base = base.where(Product.primary_operator.ilike(like))
    if purchase_level:
        base = base.where(
            Product.asin.in_(
                select(latest_join.c.asin).where(latest_join.c.purchase_level == purchase_level)
            )
        )
    if urgency:
        if urgency == "危险":
            cond = latest_join.c.inventory_days < danger
        elif urgency == "偏低":
            cond = (latest_join.c.inventory_days >= danger) & (latest_join.c.inventory_days < low)
        elif urgency == "健康":
            cond = (latest_join.c.inventory_days >= low) & (latest_join.c.inventory_days <= healthy)
        elif urgency == "过量":
            cond = latest_join.c.inventory_days > healthy
        else:
            cond = None
        if cond is not None:
            base = base.join(latest_join, Product.asin == latest_join.c.asin).where(cond)

    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await session.execute(
        base.order_by(Product.asin).offset(skip).limit(limit)
    )).scalars().all()

    asins = [p.asin for p in rows]
    latest: dict[str, dict] = {}
    if asins:
        q = (
            select(
                latest_join.c.asin,
                latest_join.c.calc_date,
                latest_join.c.forecast_total,
                latest_join.c.available_stock,
                latest_join.c.inventory_days,
                latest_join.c.replenishment_cycle,
                latest_join.c.urgency_score,
                latest_join.c.purchase_trigger,
                latest_join.c.suggested_qty,
                latest_join.c.batch_plan,
                latest_join.c.purchase_score,
                latest_join.c.purchase_level,
                latest_join.c.score_detail,
                latest_join.c.base_score,
                latest_join.c.product_stage,
                latest_join.c.product_level,
                latest_join.c.life_cycle,
            )
            .where(latest_join.c.asin.in_(asins))
        )
        for row in (await session.execute(q)).all():
            latest[row[0]] = {
                "calc_date": row[1].isoformat() if row[1] else None,
                "forecast_total": row[2],
                "available_stock": row[3],
                "inventory_days": row[4],
                "replenishment_cycle": row[5],
                "urgency_score": row[6],
                "purchase_trigger": row[7],
                "suggested_qty": row[8],
                "batch_plan": row[9],
                "purchase_score": row[10],
                "purchase_level": row[11],
                "score_detail": row[12],
                "base_score": row[13],
                "product_stage": row[14],
                "product_level": row[15],
                "life_cycle": row[16],
            }

    items = []
    for p in rows:
        r = latest.get(p.asin)
        days = r["inventory_days"] if r else None
        items.append({
            "asin": p.asin,
            "product_name": p.product_name,
            "operator": p.operator,
            "primary_operator": p.primary_operator,
            "product_level": p.product_level,
            "life_cycle": p.life_cycle,
            "product_type": p.product_type,
            "calc_date": r["calc_date"] if r else None,
            "forecast_total": r["forecast_total"] if r else None,
            "available_stock": r["available_stock"] if r else None,
            "inventory_days": days,
            "replenishment_cycle": r["replenishment_cycle"] if r else None,
            "urgency_score": r["urgency_score"] if r else None,
            "urgency_level": classify(days),
            "purchase_trigger": r["purchase_trigger"] if r else None,
            "suggested_qty": r["suggested_qty"] if r else None,
            "batch_plan": r["batch_plan"] if r else None,
            "purchase_score": r["purchase_score"] if r else None,
            "base_score": r["base_score"] if r else None,
            "product_stage": r["product_stage"] if r else None,
            "product_level": r["product_level"] if r else None,
            "life_cycle": r["life_cycle"] if r else None,
            "purchase_level": r["purchase_level"] if r else None,
            "score_detail": r["score_detail"] if r else None,
        })

    return {
        "total": total,
        "items": items,
        "thresholds": {"danger": danger, "low": low, "healthy": healthy},
    }


@router.get("/results/{asin}/latest", response_model=CalculationResultResponse)
async def get_latest_result(asin: str, session: AsyncSession = Depends(get_session)):
    """获取最新计算结果（含每步溯源）"""
    query = (
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date.desc())
        .limit(1)
    )
    result = await session.execute(query)
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"ASIN {asin} 暂无计算结果")
    # 附带品名，供计算详情页“ASIN + 品名”展示
    data = CalculationResultResponse.model_validate(row).model_dump()
    prod = (await session.execute(
        select(Product.product_name, Product.product_stage).where(Product.asin == asin)
    )).one_or_none()
    if prod:
        data["product_name"] = prod[0]
        if not data.get("product_stage"):
            data["product_stage"] = prod[1]
    return data


@router.get("/results/{asin}/latest/steps")
async def get_latest_result_steps(asin: str, session: AsyncSession = Depends(get_session)):
    """获取最新计算结果的每一步溯源明细（输入/输出/原因）"""
    # 最新计算结果
    result = await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date.desc())
        .limit(1)
    )
    calc = result.scalar_one_or_none()
    if calc is None:
        raise HTTPException(status_code=404, detail=f"ASIN {asin} 暂无计算结果")

    # 查询步骤明细
    steps_result = await session.execute(
        select(CalculationStepResult)
        .where(CalculationStepResult.calculation_id == calc.id)
        .order_by(CalculationStepResult.step_no)
    )
    steps = steps_result.scalars().all()

    return {
        "calculation_id": calc.id,
        "asin": asin,
        "calc_date": calc.calc_date.isoformat(),
        "purchase_score": calc.purchase_score,
        "base_score": calc.base_score,
        "product_stage": calc.product_stage,
        "product_level": calc.product_level,
        "life_cycle": calc.life_cycle,
        "purchase_level": calc.purchase_level,
        "suggested_qty": calc.suggested_qty,
        "steps": [
            {
                "step_no": s.step_no,
                "step_name": s.step_name,
                "status": s.status,
                "input": _parse_json(s.input_data),
                "output": _parse_json(s.output_data),
                "reason": s.reason,
                "computed_at": s.computed_at.isoformat() if s.computed_at else None,
            }
            for s in steps
        ],
    }


@router.get("/results/{asin}/history")
async def get_result_history(asin: str, session: AsyncSession = Depends(get_session)):
    """获取单ASIN历史计算结果（按日期升序，用于趋势图/溯源对比）"""
    result = await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date)
    )
    rows = result.scalars().all()
    return [
        {
            "calc_date": r.calc_date.isoformat(),
            "purchase_score": r.purchase_score,
            "base_score": r.base_score,
            "product_stage": r.product_stage,
            "product_level": r.product_level,
            "life_cycle": r.life_cycle,
            "suggested_qty": r.suggested_qty,
            "inventory_days": r.inventory_days,
            "replenishment_cycle": r.replenishment_cycle,
            "purchase_level": r.purchase_level,
            "purchase_trigger": r.purchase_trigger,
        }
        for r in rows
    ]


@router.post("/results/feedback")
async def submit_feedback(payload: FeedbackRequest, session: AsyncSession = Depends(get_session)):
    """写入人工反馈：按 ASIN(+日期) 定位计算结果，写入反馈内容与时间（供 AI 评估参考）"""
    if not payload.feedback or not payload.feedback.strip():
        raise HTTPException(status_code=400, detail="反馈内容不能为空")

    query = select(CalculationResult).where(CalculationResult.asin == payload.asin)
    if payload.calc_date:
        query = query.where(CalculationResult.calc_date == payload.calc_date)
    else:
        query = query.order_by(CalculationResult.calc_date.desc())
    result = (await session.execute(query)).scalars().first()
    if result is None:
        detail = f"ASIN {payload.asin}" + (f" 在 {payload.calc_date.isoformat()}" if payload.calc_date else "") + " 暂无计算结果，无法写入反馈"
        raise HTTPException(status_code=404, detail=detail)

    result.user_feedback = payload.feedback.strip()
    result.feedback_at = datetime.now()
    if payload.operator:
        result.operator_confirmed = payload.operator
    await session.commit()
    logger.info("已写入反馈 ASIN=%s calc_date=%s feedback=%s", payload.asin, result.calc_date, payload.feedback)
    return {
        "asin": payload.asin,
        "calc_date": result.calc_date.isoformat(),
        "user_feedback": result.user_feedback,
        "feedback_at": result.feedback_at.isoformat(),
    }


@router.post("/results/adopt")
async def submit_adopt(payload: AdoptRequest, session: AsyncSession = Depends(get_session)):
    """标记/取消 采纳：按 ASIN(+日期) 定位计算结果，记录该日/该ASIN 分析已被采纳（分析正确率为 confidence）"""
    query = select(CalculationResult).where(CalculationResult.asin == payload.asin)
    if payload.calc_date:
        query = query.where(CalculationResult.calc_date == payload.calc_date)
    else:
        query = query.order_by(CalculationResult.calc_date.desc())
    result = (await session.execute(query)).scalars().first()
    if result is None:
        detail = f"ASIN {payload.asin}" + (f" 在 {payload.calc_date.isoformat()}" if payload.calc_date else "") + " 暂无计算结果，无法采纳"
        raise HTTPException(status_code=404, detail=detail)

    result.adopted = payload.adopted
    if payload.adopted:
        result.adopted_at = datetime.now()
        result.adopted_by = (payload.operator or "").strip() or result.operator_confirmed or None
        result.adopted_confidence = payload.confidence
    else:
        result.adopted_at = None
        result.adopted_by = None
        result.adopted_confidence = None
    await session.commit()
    logger.info("已%s采纳 ASIN=%s calc_date=%s confidence=%s", "标记" if payload.adopted else "取消", payload.asin, result.calc_date, result.adopted_confidence)
    return {
        "asin": payload.asin,
        "calc_date": result.calc_date.isoformat(),
        "adopted": result.adopted,
        "adopted_at": result.adopted_at.isoformat() if result.adopted_at else None,
        "adopted_by": result.adopted_by,
        "adopted_confidence": result.adopted_confidence,
    }


@router.get("/risks")
async def get_purchase_risks(session: AsyncSession = Depends(get_session)):
    """实时采购风险提醒：断货 / 库存积压 / 利润风险（含 ASIN/等级/原因）"""
    today = date.today()
    healthy_days = await config_service.get_param(session, "inventory_healthy_max_days") or 90
    # 只统计最新计算逻辑（logic_version=1）的结果
    today_rows = (await session.execute(
        select(CalculationResult).where(
            CalculationResult.calc_date == today,
            CalculationResult.logic_version == 1,
        )
    )).scalars().all()
    rows = today_rows if today_rows else await _latest_calculation_rows(session)
    # 仅统计在售产品（排除停售/已删除）
    active_rows = await session.execute(
        select(Product.asin).where(
            Product.asin.in_([r.asin for r in rows]), Product.status == True  # noqa: E712
        )
    )
    active_set = {r[0] for r in active_rows.all()}
    rows = [r for r in rows if r.asin in active_set]

    asins = [r.asin for r in rows]
    prod_info: dict[str, tuple] = {}
    if asins:
        prods = await session.execute(
            select(Product.asin, Product.product_level, Product.profit_rate)
            .where(Product.asin.in_(asins))
        )
        for row in prods.all():
            prod_info[row[0]] = (row[1], row[2])

    stockout, overstock, profit = [], [], []
    for r in rows:
        days = r.inventory_days
        cycle = r.replenishment_cycle
        product_level, profit_rate = prod_info.get(r.asin, (None, None))
        if days is not None and cycle is not None and days < cycle:
            stockout.append({
                "asin": r.asin,
                "product_level": product_level,
                "purchase_level": r.purchase_level,
                "inventory_days": days,
                "replenishment_cycle": cycle,
                "reason": f"库存仅覆盖{days}天，低于补货周期{cycle}天",
            })
        if days is not None and days > healthy_days:
            overstock.append({
                "asin": r.asin,
                "product_level": product_level,
                "purchase_level": r.purchase_level,
                "inventory_days": days,
                "reason": f"库存覆盖{days}天，超过{healthy_days}天警戒线",
            })
        if profit_rate is not None and profit_rate < 0:
            profit.append({
                "asin": r.asin,
                "product_level": product_level,
                "purchase_level": r.purchase_level,
                "profit_rate": profit_rate,
                "reason": f"利润率{profit_rate * 100:.1f}%，处于亏损状态",
            })

    stockout.sort(key=lambda x: x["inventory_days"])
    overstock.sort(key=lambda x: -x["inventory_days"])
    profit.sort(key=lambda x: x["profit_rate"])
    return {
        "date": today.isoformat(),
        "stockout": stockout[:20],
        "overstock": overstock[:20],
        "profit": profit[:20],
    }


@router.get("/cost-table/{asin}")
async def get_cost_table(
    asin: str,
    price: Optional[float] = Query(None, description="模拟售价（如竞对旺季售价），不传则用产品当前售价"),
    session: AsyncSession = Depends(get_session),
):
    """三渠道成本表利润（海运/空派/快递）+ 盈利结论，支持竞对售价模拟重算"""
    from app.services import new_product_policy

    product = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    # 与计算流程一致：读取可配置成本参数（汇率/费率/重量兜底等）
    from app.tasks.calculation_tasks import _get_new_product_cfg
    from app.models.product_cost import ProductCost

    cfg = await _get_new_product_cfg(session)
    cost_row = (await session.execute(
        select(ProductCost).where(ProductCost.asin == asin)
    )).scalar_one_or_none()
    override = {}
    if cost_row is not None:
        override = {
            k: getattr(cost_row, k)
            for k in (
                "price", "cost_cny", "exchange_rate", "length_cm", "width_cm", "height_cm",
                "weight_kg", "freight_sea_cny", "freight_air_cny", "freight_express_cny",
                "sorting_fee", "referral_fee", "packing_fee", "inbound_fee", "storage_fee",
                "ad_fee", "return_loss", "over_threshold_loss", "misc_fee", "notes",
            )
            if getattr(cost_row, k) is not None
        }
    table = new_product_policy.calc_cost_table(
        product, cfg=cfg, price_override=price, override=override
    )
    ok, reason = new_product_policy.check_cost_table(table)
    return {
        "asin": asin,
        "product_name": product.product_name,
        "price": table["price"],
        "is_peak": table.get("is_peak"),
        "unit_cost": table["unit_cost"],
        "cost_price_cny": _to_float(product.cost_price),
        "exchange_rate": table.get("exchange_rate"),
        "freight_basis": table.get("freight_basis"),
        "weight_kg": table.get("weight_kg"),
        "fba_fee": table["fba_fee"],
        "referral_fee": table["referral_fee"],
        "extra_fees_usd": table.get("extra_fees_usd") or {},
        "extra_fees_total": table.get("extra_fees_total") or 0,
        "channels": table["channels"],
        "profitable_modes": table["profitable_modes"],
        "all_profitable": table["all_profitable"],
        "conclusion_ok": ok,
        "conclusion": reason,
    }


@router.post("/trigger/batch")
async def trigger_batch_calculation():
    """手动触发批量计算（全量重算，异步任务，含实时进度）"""
    progress = {"total": 0, "done": 0, "percent": 0, "current_asin": None}
    job_id = _start_job(lambda: run_batch_calculation(progress=progress), progress=progress)
    return {
        "message": "批量计算已提交",
        "job_id": job_id,
        "status": "running",
    }


@router.get("/due-stats")
async def due_calculation_stats(session: AsyncSession = Depends(get_session)):
    """查看按等级频率计算的到期情况（不执行计算）"""
    return await get_due_calculation_stats(session)


@router.get("/next-calculation")
async def next_calculation_info(session: AsyncSession = Depends(get_session)):
    """仪表盘预告：下一次自动分析时间 + 涉及的产品等级 + 到期产品数（不执行计算）"""
    return await get_next_calculation_info(session)


@router.post("/timeline/reset")
async def timeline_reset(payload: TimelineResetRequest, session: AsyncSession = Depends(get_session)):
    """排程重置(时间调节器)：把下次分析时间线拨到指定等级在 target_date 到期（不动频率配置）"""
    try:
        result = await reset_calculation_timeline(
            session, levels=payload.levels, target_date=payload.target_date
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/timeline/resets")
async def list_timeline_resets(session: AsyncSession = Depends(get_session)):
    """查看当前有效的排程重置覆盖列表"""
    rows = (await session.execute(
        select(CalculationTimelineReset)
        .order_by(CalculationTimelineReset.target_date, CalculationTimelineReset.asin)
    )).scalars().all()
    return [
        {
            "asin": r.asin,
            "override_last_date": r.override_last_date.isoformat(),
            "target_date": r.target_date.isoformat(),
        }
        for r in rows
    ]


@router.delete("/timeline/reset")
async def clear_timeline_resets(session: AsyncSession = Depends(get_session)):
    """清空全部排程重置覆盖（恢复常态排程）"""
    # 删除前保留数量用于反馈
    count = (await session.execute(
        select(func.count()).select_from(CalculationTimelineReset)
    )).scalar() or 0
    await session.execute(delete(CalculationTimelineReset))
    await session.commit()
    return {"message": "已清空排程重置", "cleared_count": count}


@router.post("/trigger/due")
async def trigger_due_calculation():
    """手动触发按等级频率计算（异步任务，含实时进度）"""
    progress = {"total": 0, "done": 0, "percent": 0, "current_asin": None}
    job_id = _start_job(lambda: run_due_calculation(progress=progress), progress=progress)
    return {
        "message": "按频率计算已提交",
        "job_id": job_id,
        "status": "running",
    }


@router.post("/trigger/level/{level}")
async def trigger_level_calculation(
    level: str,
    lifecycles: Optional[str] = Query(None, description="按生命周期过滤，逗号分隔，如 启动期,增长期,热卖期；不传=全部"),
):
    """立即计算指定等级（S/A/B/C/D），忽略频率（异步任务，含实时进度）"""
    progress = {"total": 0, "done": 0, "percent": 0, "current_asin": None}
    job_id = _start_job(
        lambda: run_level_calculation(level, progress=progress, lifecycles=lifecycles),
        progress=progress,
    )
    return {
        "message": f"{level.upper()} 级计算已提交",
        "job_id": job_id,
        "status": "running",
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str):
    """查询后台计算任务状态"""
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _fmt_score_detail(sd) -> str:
    """格式化评分明细（六维评分 + 特例/覆盖判定）"""
    if not isinstance(sd, dict) or not sd:
        return "  暂无评分明细"
    dims_source = sd.get("六维评分")
    if not isinstance(dims_source, dict):
        dims_source = sd
    dims = [
        (k, v) for k, v in dims_source.items()
        if isinstance(v, dict) and isinstance(v.get("value"), (int, float))
    ]
    skip = {"level", "suggested_qty", "reason", "steps", "cost_table", "triggered", "六维评分"}
    specials = {
        k: v for k, v in sd.items()
        if k != "六维评分"
        and k not in skip
        and not (isinstance(v, dict) and isinstance(v.get("value"), (int, float)))
    }
    out = []
    if dims:
        out.append("【六维评分】")
        for k, v in dims:
            label = v.get("label") or k
            raw = v.get("value") or 0
            weight = v.get("weight")
            w = f"（权重 {round(float(weight) * 100)}%）" if isinstance(weight, (int, float)) else ""
            out.append(f"  {label}: {raw}分{w}")
    if specials:
        out.append("【特例/覆盖判定】")
        for k, v in specials.items():
            out.append(f"  {k}: {_fmt_special_val(v)}")
    return "\n".join(out) if out else "  暂无评分明细"


def _fmt_special_val(v) -> str:
    if isinstance(v, dict):
        parts = [
            f"{f}={v[f]}" for f in ("level", "qty", "ai_qty", "conclusion", "lifecycle", "source", "name", "id")
            if v.get(f) not in (None, "")
        ]
        base = (v.get("detail") or "").strip()
        if parts:
            base = f"（{'；'.join(parts)}）{base}"
        return base or json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        out = []
        for i in v:
            if isinstance(i, dict):
                name = i.get("name") or i.get("id")
                detail = (i.get("detail") or "").strip()
                out.append(f"{name}: {detail}" if name else detail)
            else:
                out.append(str(i))
        return "；".join(out) if out else "-"
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _format_private_report(result: dict) -> str:
    """将单ASIN计算结果格式化为私发文本（与网页数据分析详情同等详细）"""
    score = result.get("purchase_score")
    stock = result.get("available_stock")
    days = result.get("inventory_days")
    cycle = result.get("replenishment_cycle")
    fore = result.get("forecast_total")
    urg = result.get("urgency_score")
    reason = (result.get("reason") or "").strip()
    if not reason:
        ai = result.get("ai_analysis") or {}
        reason = (ai.get("reason") or ai.get("conclusion") or "").strip()

    lines = [
        "📊 自动补货决策 · 单ASIN分析完成",
        f"产品: {result.get('product_name') or '—'} ({result.get('asin', '')})",
        f"分析日期: {result.get('calc_date', '—')} ｜ 生命周期: {result.get('life_cycle') or '—'}",
        "",
        "【采购决策】",
        f"决策: {result.get('purchase_trigger') or '—'} ｜ 级别: {result.get('purchase_level') or '—'} ｜ 评分: {score if score is not None else '—'}分",
        f"建议采购: {int(result.get('suggested_qty') or 0):,} 件",
        f"可用库存: {stock if stock is not None else '—'} 件 ｜ 预测总销量: {fore if fore is not None else '—'} 件",
        f"库存覆盖: {days if days is not None else '—'} 天 ｜ 补货周期: {cycle if cycle is not None else '—'} 天 ｜ 紧急程度: {urg if urg is not None else '—'}",
    ]
    batches = (result.get("batch_plan") or {}).get("batches") or []
    if batches:
        for b in batches:
            lines.append(
                f"建议批次: 第{b.get('batch_no', '—')}批 {b.get('method') or ''} "
                f"到货{b.get('days', '—')}天 {b.get('qty', '—')}件"
            )
    if reason:
        lines.append(f"原因: {reason}")

    # 评分明细
    lines.append("")
    lines.append("【评分明细】")
    lines.append(_fmt_score_detail(result.get("score_detail")))

    # DeepSeek AI 评估
    lines.append("")
    ai = result.get("ai_analysis")
    if ai:
        lines.append("【🤖 DeepSeek AI 评估】")
        factors = ai.get("factors") or {}
        if factors:
            lines.append("多维评估:")
            lines.extend(f"  {k}: {v}" for k, v in factors.items())
        fc = ai.get("forecast") or {}
        if fc:
            lines.append(f"📈 未来销量评估: {fc.get('assessment') or ''}")
            if fc.get("suggested_forecast_total") is not None:
                lines.append(f"  AI建议预测总量: {int(fc['suggested_forecast_total']):,} ｜ 趋势: {fc.get('trend') or '—'}")
        ai_parts = [f"结论: {ai.get('conclusion') or '—'}"]
        if ai.get("suggested_qty") is not None:
            ai_parts.append(f"建议数量: {int(ai['suggested_qty']):,}")
        if ai.get("confidence"):
            ai_parts.append(f"信心: {ai['confidence']}")
        lines.append("  " + " ｜ ".join(ai_parts))
        if ai.get("reason"):
            lines.append(f"理由: {ai['reason']}")
        risks = ai.get("risks") or []
        if risks:
            lines.append("风险:")
            lines.extend(f"  • {r}" for r in risks)
    else:
        lines.append("【🤖 DeepSeek AI 评估】本次未启用/未获取AI评估")

    # 成本表（三渠道 Profit）
    lines.append("")
    ct = result.get("cost_table")
    if ct:
        ch = ct.get("channels") or {}
        lines.append("【📊 成本表（三渠道 Profit）】")
        season = "旺季" if ct.get("is_peak") else "淡季"
        basis = "按重量计费" if ct.get("freight_basis") == "weight" else "按件计费"
        sea_fee = (ch.get("sea") or {}).get("freight_fee")
        air_fee = (ch.get("air") or {}).get("freight_fee")
        exp_fee = (ch.get("express") or {}).get("freight_fee")
        lines.append(f"售价 ${ct.get('price')} ｜ 运费阶段: {season}（海运{sea_fee}￥/空派{air_fee}￥/快递{exp_fee}￥） ｜ {basis}")
        lines.append("渠道 | 售价($) | 运费($) | 单件利润($) | 毛利率 | 盈亏")
        for m in ("sea", "air", "express"):
            c = ch.get(m)
            if not c:
                continue
            margin = f"{(c['margin'] * 100):.1f}%" if c.get("margin") is not None else "-"
            lines.append(
                f"{c.get('label')} | ${ct.get('price')} | ${c.get('freight_fee_usd')} | "
                f"${c.get('profit')} | {margin} | {'盈利' if c.get('profitable') else '亏损'}"
            )
        if ct.get("cost_price_cny") is not None:
            lines.append(
                f"采购成本: ¥{ct.get('cost_price_cny')}/件（≈${ct.get('unit_cost')} USD，汇率 {ct.get('exchange_rate')}）"
            )
        ck = result.get("cost_check") or {}
        if ck.get("reason"):
            lines.append(f"结论: {ck['reason']}")
    else:
        lines.append("【📊 成本表（三渠道 Profit）】无成本表数据")

    # 历史对比趋势
    lines.append("")
    history = result.get("history") or []
    lines.append("【历史对比趋势】")
    if len(history) < 2:
        lines.append("  暂无足够历史数据，连续多日计算后将形成趋势")
    else:
        for h in history[-10:]:
            lines.append(
                f"  {h['calc_date']}: 评分{h['purchase_score'] if h['purchase_score'] is not None else '—'} / "
                f"建议{int(h['suggested_qty'] or 0):,}件 / 库存{h['inventory_days'] if h['inventory_days'] is not None else '—'}天 / {h['purchase_level'] or '—'}"
            )

    return "\n".join(lines)


async def _notify_operator_private(asin: str, result: dict, session: AsyncSession) -> dict:
    """计算成功后私发分析结果给对应运营负责人（飞书单聊）

    通过 Product.primary_operator → Operator.feishu_user_id 匹配接收人；
    负责人未绑定飞书UID / 未配置应用机器人 / 发送失败等情况均不阻断主流程，仅记录日志。
    """
    prod = (await session.execute(
        select(Product.primary_operator).where(Product.asin == asin)
    )).scalar_one_or_none()
    if not prod:
        return {"sent": False, "reason": "产品未配置负责人"}
    from app.services.operator_sync import clean_operator_name
    primary = clean_operator_name(prod)
    if not primary:
        return {"sent": False, "reason": "产品负责人为空"}
    uid = (await session.execute(
        select(Operator.feishu_user_id).where(Operator.name == primary)
    )).scalar_one_or_none()
    if not uid:
        return {"sent": False, "reason": f"负责人「{primary}」未绑定飞书UID", "operator": primary}

    notifier = FeishuNotifier()
    if notifier.app_mode:
        err = await notifier.send_private_text(uid, _format_private_report(result))
        if err is None:
            logger.info("已私发分析结果 ASIN=%s → 负责人「%s」", asin, primary)
            return {"sent": True, "operator": primary}
        logger.warning("飞书私发失败 ASIN=%s → 负责人「%s」: %s", asin, primary, err)
        return {"sent": False, "reason": err, "operator": primary}
    return {"sent": False, "reason": "飞书应用机器人未配置", "operator": primary}


async def _run_single_with_sync(asin: str, progress: dict | None = None) -> dict:
    """手动分析流程：先全量重拉三项数据（销量/库存/待到货量）→ 单品计算 → 私发负责人

    三项同步为全量同步（与定时任务同源同口径），耗时较长，故整体作为后台任务执行并回报进度。
    """
    from app.tasks.sync_tasks import (
        sync_sales_data, sync_inventory, sync_purchase_orders, sync_purchase_order_items,
    )

    steps = [
        ("同步销量数据", sync_sales_data),
        ("同步库存数据", sync_inventory),
        ("同步待到货量", sync_purchase_orders),
        ("同步采购单产品明细", lambda: sync_purchase_order_items(mode="recent30")),
    ]
    total_steps = len(steps) + 1
    if progress is not None:
        progress["total"] = total_steps
        progress["done"] = 0
        progress["percent"] = 0
        progress["current_asin"] = asin
        progress["stage"] = steps[0][0]

    for i, (name, fn) in enumerate(steps):
        if progress is not None:
            progress["stage"] = name
        try:
            await fn()
        except Exception as e:  # noqa: BLE001
            logger.error("手动分析前%s失败 ASIN=%s: %s", name, asin, e)
        if progress is not None:
            progress["done"] = i + 1
            progress["percent"] = round((i + 1) / total_steps * 100)

    if progress is not None:
        progress["stage"] = "计算分析"

    stats: dict = {"asin": asin}
    async with async_session_factory() as session:
        result = await run_single_calculation(asin, session)
        await session.commit()

        if result.get("skipped"):
            stats.update({"skipped": True, "reason": result.get("reason")})
        elif "error" in result:
            stats.update({"error": result["error"]})
        else:
            # 计算成功后，私发分析结果给对应运营负责人（失败不阻断）
            stats["notify"] = await _notify_operator_private(asin, result, session)

    if progress is not None:
        progress["done"] = total_steps
        progress["percent"] = 100
        progress["stage"] = None
    return stats


@router.post("/trigger/{asin}")
async def trigger_single_calculation(asin: str):
    """手动触发单个ASIN计算：先全量重拉三项数据（销量/库存/待到货量），再计算并私发负责人"""
    progress = {"total": 0, "done": 0, "percent": 0, "current_asin": asin, "stage": None}
    job_id = _start_job(lambda: _run_single_with_sync(asin, progress=progress), progress=progress)
    return {
        "message": f"ASIN {asin} 已提交：正在重拉三项数据后分析",
        "job_id": job_id,
        "status": "running",
    }


async def _load_latest_result_for_notify(asin: str, session: AsyncSession) -> dict | None:
    """从库中还原某 ASIN 最新一次计算结果的完整字典（用于「立刻发送」不重算、直接私发）"""
    row = (await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None

    prod = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()

    # AI 评估：取该 ASIN 最新一次成功的采购评估（与计算结果同源）
    ai_analysis = None
    ai_row = (await session.execute(
        select(AiEvaluation.output_data)
        .where(
            AiEvaluation.asin == asin,
            AiEvaluation.eval_type == "purchase_advice",
            AiEvaluation.status == "success",
        )
        .order_by(AiEvaluation.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if ai_row:
        ai_analysis = _parse_json(ai_row)
        if not isinstance(ai_analysis, dict):
            ai_analysis = None

    # 成本表（与 /cost-table 接口同口径重算）
    cost_table = None
    cost_ok, cost_reason = None, ""
    try:
        from app.services import new_product_policy
        from app.tasks.calculation_tasks import _get_new_product_cfg
        from app.models.product_cost import ProductCost

        if prod is not None:
            cfg = await _get_new_product_cfg(session)
            cost_row = (await session.execute(
                select(ProductCost).where(ProductCost.asin == asin)
            )).scalar_one_or_none()
            override = {}
            if cost_row is not None:
                override = {
                    k: getattr(cost_row, k)
                    for k in (
                        "price", "cost_cny", "exchange_rate", "length_cm", "width_cm", "height_cm",
                        "weight_kg", "freight_sea_cny", "freight_air_cny", "freight_express_cny",
                        "sorting_fee", "referral_fee", "packing_fee", "inbound_fee", "storage_fee",
                        "ad_fee", "return_loss", "over_threshold_loss", "misc_fee", "notes",
                    )
                    if getattr(cost_row, k) is not None
                }
            cost_table = new_product_policy.calc_cost_table(prod, cfg=cfg, override=override)
            cost_ok, cost_reason = new_product_policy.check_cost_table(cost_table)
    except Exception as e:  # noqa: BLE001
        logger.warning("还原成本表失败 ASIN=%s: %s", asin, e)
        cost_table = None

    # 历史对比趋势
    history_rows = (await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date)
    )).scalars().all()
    history = [
        {
            "calc_date": r.calc_date.isoformat(),
            "purchase_score": r.purchase_score,
            "suggested_qty": r.suggested_qty,
            "inventory_days": r.inventory_days,
            "purchase_level": r.purchase_level,
            "purchase_trigger": r.purchase_trigger,
        }
        for r in history_rows
    ]

    batch_plan = _parse_json(row.batch_plan)
    if not isinstance(batch_plan, dict):
        batch_plan = {}
    score_detail = _parse_json(row.score_detail)

    return {
        "asin": asin,
        "product_name": prod.product_name if prod else None,
        "calc_date": row.calc_date.isoformat(),
        "life_cycle": row.life_cycle,
        "forecast_total": row.forecast_total,
        "available_stock": row.available_stock,
        "inventory_days": row.inventory_days,
        "replenishment_cycle": row.replenishment_cycle,
        "urgency_score": row.urgency_score,
        "purchase_trigger": row.purchase_trigger,
        "suggested_qty": row.suggested_qty,
        "batch_plan": batch_plan,
        "purchase_score": row.purchase_score,
        "purchase_level": row.purchase_level,
        "score_detail": score_detail,
        "ai_analysis": ai_analysis,
        "reason": "",
        "cost_table": cost_table,
        "cost_check": {"ok": cost_ok, "reason": cost_reason},
        "history": history,
    }


@router.post("/trigger/{asin}/notify")
async def notify_single_result(asin: str, session: AsyncSession = Depends(get_session)):
    """将某 ASIN 最新一次分析报告私发给对应运营负责人（不重算，仅发送）"""
    result = await _load_latest_result_for_notify(asin, session)
    if result is None:
        raise HTTPException(status_code=404, detail=f"ASIN {asin} 暂无计算结果，请先执行「立即分析」")
    notify = await _notify_operator_private(asin, result, session)
    if not notify.get("sent"):
        return {
            "message": f"未发送：{notify.get('reason', '未知原因')}",
            "notify": notify,
            "calc_date": result["calc_date"],
        }
    return {
        "message": f"已将 {asin}（{result['calc_date']}）的分析报告私发给负责人「{notify.get('operator')}」",
        "notify": notify,
        "calc_date": result["calc_date"],
    }


@router.get("/daily-report")
async def daily_report(
    include_results: bool = Query(True, description="是否返回全部结果明细（看板可传 false 减少负载）"),
    session: AsyncSession = Depends(get_session),
):
    """获取今日日报摘要"""
    summary = await get_daily_summary(session, include_results=include_results)
    return summary


@router.post("/daily-report/push")
async def push_daily_report(session: AsyncSession = Depends(get_session)):
    """手动推送日报到飞书"""
    summary = await get_daily_summary(session)

    # AI 日报总结（启用时自动生成，失败不影响推送）
    if ai_eval.ai_enabled():
        try:
            summary = await ai_eval.generate_daily_report_ai(summary, session)
        except Exception as e:
            logger.warning(f"AI 日报生成失败，使用规则日报推送: {e}")

    # 群白名单：参数设置里配置，可多个群，逗号分隔
    whitelist = await config_service.get_param(session, "feishu_group_whitelist") or ""
    chat_ids = [c.strip() for c in str(whitelist).split(",") if c.strip()]
    notifier = FeishuNotifier(chat_ids=chat_ids or None)
    if not notifier.app_mode and not notifier.webhook_url:
        raise HTTPException(
            status_code=400,
            detail="飞书通知未配置，请在.env中设置 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID（应用机器人）或 FEISHU_WEBHOOK_URL",
        )
    if notifier.app_mode and not notifier.chat_id:
        raise HTTPException(
            status_code=400,
            detail="飞书应用机器人未配置接收群，请在.env中设置 FEISHU_CHAT_ID（可运行 tools/feishu_discover_chats.py 查看机器人所在群）",
        )

    success = await notifier.send_daily_report(summary)
    if not success:
        raise HTTPException(status_code=500, detail="飞书消息发送失败")

    # 日报可视化大屏图片（失败不影响文本日报推送结果）
    image_ok = False
    if notifier.app_mode:
        try:
            from app.services.report_image import generate_and_send
            image_ok = await generate_and_send(notifier)
        except Exception as e:
            logger.error(f"日报大屏图片发送失败: {e}")

    return {
        "message": "日报已推送至飞书",
        "image_sent": image_ok,
        "summary": {
            "total_asins": summary["total_asins"],
            "immediate_count": summary["immediate_count"],
            "observe_count": summary["observe_count"],
            "pause_count": summary["pause_count"],
        },
    }


# ==================== 任务大厅（自定义任务） ====================

async def _run_multi_asin_task(levels: str, lifecycles: str | None, with_report: bool,
                               with_image: bool, progress: dict | None = None) -> dict:
    """任务大厅-多ASIN任务：按「等级 + 生命周期」立即计算，可选生成并推送日报/大屏大图"""
    if progress is not None:
        progress["stage"] = "同步采购单产品明细"
    try:
        from app.tasks.sync_tasks import sync_purchase_order_items

        await sync_purchase_order_items(mode="recent30")
    except Exception as e:  # noqa: BLE001
        logger.error("任务大厅-多ASIN 同步采购单产品明细失败: %s", e)
    stats = await run_level_calculation(levels, progress=progress, lifecycles=lifecycles)
    result: dict = {
        "levels": levels,
        "lifecycles": lifecycles,
        "calc": {k: stats.get(k) for k in ("total", "success", "failed", "immediate", "observe", "pause")},
    }
    if not with_report:
        return result

    if progress is not None:
        progress["stage"] = "生成并推送日报"
    async with async_session_factory() as session:
        summary = await get_daily_summary(session, levels=levels, lifecycles=lifecycles)
        if ai_eval.ai_enabled():
            try:
                summary = await ai_eval.generate_daily_report_ai(summary, session)
            except Exception as e:  # noqa: BLE001
                logger.warning("AI 日报生成失败，使用规则日报推送: %s", e)
        whitelist = await config_service.get_param(session, "feishu_group_whitelist") or ""
    chat_ids = [c.strip() for c in str(whitelist).split(",") if c.strip()]
    notifier = FeishuNotifier(chat_ids=chat_ids or None)
    if not notifier.app_mode and not notifier.webhook_url:
        result["report"] = {"sent": False, "reason": "飞书通知未配置"}
        return result
    ok = await notifier.send_daily_report(summary)
    image_ok = False
    if ok and with_image and notifier.app_mode:
        try:
            from app.services.report_image import generate_and_send

            image_ok = await generate_and_send(notifier)
        except Exception as e:  # noqa: BLE001
            logger.error("日报大屏图片发送失败: %s", e)
    result["report"] = {
        "sent": ok,
        "image_sent": image_ok,
        "total_asins": summary.get("total_asins"),
        "immediate_count": summary.get("immediate_count"),
        "observe_count": summary.get("observe_count"),
        "pause_count": summary.get("pause_count"),
    }
    if progress is not None:
        progress["stage"] = None
    return result


@router.post("/task-hall/multi")
async def trigger_task_hall_multi(
    levels: str = Query(..., description="产品等级，如 SA / S,A,B"),
    lifecycles: Optional[str] = Query(None, description="生命周期，逗号分隔，如 启动期,增长期,热卖期；不传=全部"),
    with_report: bool = Query(True, description="是否生成并推送日报到飞书"),
    with_image: bool = Query(False, description="是否附带推送日报大屏大图（需 with_report=true）"),
):
    """任务大厅-多ASIN任务：立即按「等级 + 生命周期」跑完整流程（计算 → 日报 → 推送）"""
    if not _normalize_levels(levels):
        raise HTTPException(status_code=400, detail=f"无效产品等级: {levels}，支持 S/A/B/C/D")
    progress = {"total": 0, "done": 0, "percent": 0, "current_asin": None, "stage": "计算"}
    job_id = _start_job(
        lambda: _run_multi_asin_task(levels, lifecycles, with_report, with_image, progress),
        progress=progress,
        kind="task-hall-multi",
    )
    return {"message": "多ASIN任务已提交", "job_id": job_id, "status": "running"}


@router.get("/task-hall/multi/latest")
async def latest_task_hall_multi():
    """任务大厅-多ASIN任务：返回最近一次任务，供页面刷新后恢复进度/结果"""
    jobs = [j for j in _jobs.values() if j.get("kind") == "task-hall-multi"]
    if not jobs:
        return {"job": None}
    return {"job": max(jobs, key=lambda j: j["started_at"])}


# 任务大厅-单ASIN定时私发任务（内存登记；API 重启后待执行任务丢失，需重新创建）
_single_tasks: dict[str, dict] = {}


async def _run_single_notify_task(task: dict) -> None:
    """执行单ASIN报告私发任务：全量重拉三项数据 → 计算 → 私发负责人"""
    task["status"] = "running"
    task["started_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        task["stats"] = await _run_single_with_sync(task["asin"])
        task["status"] = "done"
    except Exception as e:  # noqa: BLE001
        task["status"] = "failed"
        task["error"] = str(e)
        logger.error("任务大厅单ASIN任务执行失败 ASIN=%s: %s", task["asin"], e)
    finally:
        task["finished_at"] = datetime.now().isoformat(timespec="seconds")


@router.post("/task-hall/single")
async def create_task_hall_single(
    asin: str = Query(..., description="ASIN"),
    operator: Optional[str] = Query(None, description="负责人姓名；填写时需与该ASIN产品负责人一致，防止发错人"),
    run_at: Optional[str] = Query(None, description="定时执行时间（ISO，如 2026-10-08T09:00）；不传=立即执行"),
    session: AsyncSession = Depends(get_session),
):
    """任务大厅-单ASIN报告私发任务：立即或定时私发某 ASIN 分析结果给负责人"""
    asin = (asin or "").strip()
    if not asin:
        raise HTTPException(status_code=400, detail="ASIN 不能为空")
    product = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail=f"未找到 ASIN {asin}")

    from app.services.operator_sync import clean_operator_name

    product_operator = clean_operator_name(product.primary_operator)
    op = (operator or "").strip()
    if op and op != product_operator:
        raise HTTPException(
            status_code=400,
            detail=f"负责人不匹配：ASIN {asin} 实际负责人为「{product_operator or '未配置'}」，与所选「{op}」不一致",
        )

    task_id = uuid.uuid4().hex[:12]
    task = {
        "id": task_id,
        "asin": asin,
        "operator": op or product_operator,
        "product_operator": product_operator,
        "run_at": run_at,
        "status": "pending",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "started_at": None,
        "finished_at": None,
        "stats": None,
        "error": None,
    }

    if run_at:
        from apscheduler.triggers.date import DateTrigger

        from app.tasks.scheduler import get_scheduler

        scheduler = get_scheduler()
        if scheduler is None:
            raise HTTPException(status_code=503, detail="调度器未启动，无法创建定时任务")
        try:
            run_dt = datetime.fromisoformat(run_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="定时时间格式不正确（应为 ISO，如 2026-10-08T09:00）")
        if run_dt <= datetime.now():
            raise HTTPException(status_code=400, detail="定时时间需晚于当前时间")
        _single_tasks[task_id] = task
        scheduler.add_job(
            _run_single_notify_task,
            trigger=DateTrigger(run_date=run_dt),
            args=[task],
            id=f"task_hall_single_{task_id}",
            name=f"单ASIN私发 {asin}",
            replace_existing=True,
        )
        return {"message": f"定时任务已创建，将于 {run_dt:%Y-%m-%d %H:%M} 执行", "task": task}

    _single_tasks[task_id] = task
    asyncio.create_task(_run_single_notify_task(task))
    return {"message": "任务已提交，正在执行", "task": task}


@router.get("/task-hall/single")
async def list_task_hall_single():
    """任务大厅-单ASIN私发任务列表（内存登记）"""
    tasks = sorted(_single_tasks.values(), key=lambda t: t["created_at"], reverse=True)
    return {"tasks": tasks}


@router.delete("/task-hall/single/{task_id}")
async def cancel_task_hall_single(task_id: str):
    """取消（或删除）任务大厅-单ASIN私发任务"""
    task = _single_tasks.pop(task_id, None)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    from app.tasks.scheduler import get_scheduler

    scheduler = get_scheduler()
    if scheduler is not None:
        try:
            scheduler.remove_job(f"task_hall_single_{task_id}")
        except Exception:  # noqa: BLE001
            pass
    return {"message": "任务已取消"}


# ==================== 运行事实可解释（Phase 0 / G-06） ====================

@router.get("/skip-logs", summary="静默跳过记录（节日门禁等）")
async def list_skip_logs(
    calc_date: Optional[str] = Query(None, description="查询日期 YYYY-MM-DD，缺省今天"),
    gate: Optional[str] = Query(None, description="按闸门过滤，如 '节日门禁'"),
    limit: int = Query(200, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
):
    """列出当日"静默跳过"的产品（不写 calculation_results，只写 calculation_skip_logs）。"""
    try:
        d = date.fromisoformat(calc_date.strip()) if calc_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="calc_date 格式错误，应为 YYYY-MM-DD")

    q = select(CalculationSkipLog).where(CalculationSkipLog.calc_date == d)
    if gate:
        q = q.where(CalculationSkipLog.gate == gate.strip())
    rows = (await session.execute(q.order_by(CalculationSkipLog.id.desc()).limit(limit))).scalars().all()

    return {
        "calc_date": d.isoformat(),
        "count": len(rows),
        "items": [
            {
                "asin": r.asin,
                "gate": r.gate,
                "festival": r.festival,
                "festival_name": r.festival_name,
                "next_festival_date": r.next_festival_date.isoformat() if r.next_festival_date else None,
                "days_until": r.days_until,
                "reason": r.reason,
            }
            for r in rows
        ],
    }


@router.get("/run-summary", summary="当日计算漏斗：到期 / 过滤 / 实际计算 / 跳过原因")
async def calculation_run_summary(
    calc_date: Optional[str] = Query(None, description="查询日期 YYYY-MM-DD，缺省今天"),
    session: AsyncSession = Depends(get_session),
):
    """回答"今天为什么只算了 N 个"：把到期数、生命周期过滤配置、实际计算数、跳过原因并列。"""
    try:
        d = date.fromisoformat(calc_date.strip()) if calc_date else date.today()
    except ValueError:
        raise HTTPException(status_code=400, detail="calc_date 格式错误，应为 YYYY-MM-DD")

    due = await get_due_calculation_stats(session)
    allowed_lifecycles = sorted(await _load_report_lifecycles(session))

    computed = (await session.execute(
        select(func.count()).select_from(CalculationResult).where(CalculationResult.calc_date == d)
    )).scalar() or 0

    skip_rows = (await session.execute(
        select(CalculationSkipLog.gate, func.count())
        .where(CalculationSkipLog.calc_date == d)
        .group_by(CalculationSkipLog.gate)
    )).all()

    return {
        "date": d.isoformat(),
        "enabled_products": due.get("total"),
        "due": due.get("due"),
        "due_by_level": due.get("by_level"),
        "allowed_lifecycles": allowed_lifecycles or None,
        "computed": computed,
        "skipped_by_gate": {g: c for g, c in skip_rows},
        "notes": [
            "到期中若无等级/无生命周期，会在「基础数据校验」被跳过且不写任何记录（G-1）",
            "节日日期超过 180 天的产品会被「节日门禁」静默跳过并写入 skip-logs（G-2）",
            "allowed_lifecycles 非空时，只有这些生命周期的到期产品会进入计算",
            "computed 为当日实际写入 calculation_results 的条数",
        ],
    }
