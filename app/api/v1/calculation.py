"""计算任务 API 路由"""

from datetime import date
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.calculation import CalculationResult, CalculationStepResult
from app.models.product import Product
from app.schemas.calculation import CalculationResultResponse
from app.tasks.calculation_tasks import (
    run_single_calculation,
    run_batch_calculation,
    run_due_calculation,
    get_due_calculation_stats,
    get_daily_summary,
)
from app.integrations.feishu import FeishuNotifier

router = APIRouter()


def _parse_json(s: str | None):
    """JSON字符串转对象"""
    if not s:
        return None
    import json
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


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
    return row


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
            "suggested_qty": r.suggested_qty,
            "inventory_days": r.inventory_days,
            "replenishment_cycle": r.replenishment_cycle,
            "purchase_level": r.purchase_level,
            "purchase_trigger": r.purchase_trigger,
        }
        for r in rows
    ]


@router.get("/risks")
async def get_purchase_risks(session: AsyncSession = Depends(get_session)):
    """实时采购风险提醒：断货 / 库存积压 / 利润风险（含 ASIN/等级/原因）"""
    today = date.today()
    result = await session.execute(
        select(CalculationResult).where(CalculationResult.calc_date == today)
    )
    rows = result.scalars().all()

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
        if days is not None and days > 90:
            overstock.append({
                "asin": r.asin,
                "product_level": product_level,
                "purchase_level": r.purchase_level,
                "inventory_days": days,
                "reason": f"库存覆盖{days}天，超过90天警戒线",
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


@router.post("/trigger/batch")
async def trigger_batch_calculation():
    """手动触发批量计算（全量重算）"""
    stats = await run_batch_calculation()
    return {
        "message": "批量计算完成",
        "stats": stats,
    }


@router.get("/due-stats")
async def due_calculation_stats(session: AsyncSession = Depends(get_session)):
    """查看按等级频率计算的到期情况（不执行计算）"""
    return await get_due_calculation_stats(session)


@router.post("/trigger/due")
async def trigger_due_calculation():
    """手动触发按等级频率计算（S每天/A每3天/B每5天/C每7天/D每14天，仅计算到期产品）"""
    stats = await run_due_calculation()
    return {
        "message": "按频率计算完成",
        "stats": stats,
    }


@router.post("/trigger/{asin}")
async def trigger_single_calculation(
    asin: str,
    session: AsyncSession = Depends(get_session),
):
    """手动触发单个ASIN计算"""
    result = await run_single_calculation(asin, session)
    await session.commit()

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    return {
        "message": f"ASIN {asin} 计算完成",
        "data": result,
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

    notifier = FeishuNotifier()
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

    return {
        "message": "日报已推送至飞书",
        "summary": {
            "total_asins": summary["total_asins"],
            "immediate_count": summary["immediate_count"],
            "observe_count": summary["observe_count"],
            "pause_count": summary["pause_count"],
        },
    }
