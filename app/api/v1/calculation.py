"""计算任务 API 路由"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.calculation import CalculationResult
from app.schemas.calculation import CalculationResultResponse
from app.tasks.calculation_tasks import (
    run_single_calculation,
    run_batch_calculation,
    get_daily_summary,
)
from app.integrations.feishu import FeishuNotifier

router = APIRouter()


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
    """获取最新计算结果"""
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


@router.post("/trigger/batch")
async def trigger_batch_calculation():
    """手动触发批量计算"""
    stats = await run_batch_calculation()
    return {
        "message": "批量计算完成",
        "stats": stats,
    }


@router.get("/daily-report")
async def daily_report(session: AsyncSession = Depends(get_session)):
    """获取今日日报摘要"""
    summary = await get_daily_summary(session)
    return summary


@router.post("/daily-report/push")
async def push_daily_report(session: AsyncSession = Depends(get_session)):
    """手动推送日报到飞书"""
    summary = await get_daily_summary(session)

    notifier = FeishuNotifier()
    if not notifier.webhook_url:
        raise HTTPException(
            status_code=400,
            detail="飞书Webhook未配置，请在.env中设置FEISHU_WEBHOOK_URL",
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
