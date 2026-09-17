"""DeepSeek AI 评估 API"""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.ai_evaluation import AiEvaluation
from app.services import ai_eval
from app.tasks.calculation_tasks import get_daily_summary

router = APIRouter()


def _require_enabled():
    if not ai_eval.ai_enabled():
        raise HTTPException(status_code=400, detail="AI 评估未启用或未配置 DEEPSEEK_API_KEY")


@router.get("/latest/{asin}")
async def latest_evaluation(asin: str, session: AsyncSession = Depends(get_session)):
    """获取某个 ASIN 最近一次 AI 采购评估（无评估时返回 status=none）"""
    row = (await session.execute(
        select(AiEvaluation)
        .where(AiEvaluation.asin == asin, AiEvaluation.eval_type == "purchase_advice")
        .order_by(AiEvaluation.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if row is None:
        return {"status": "none", "asin": asin}
    try:
        out = json.loads(row.output_data or "{}")
    except (json.JSONDecodeError, TypeError):
        out = {}
    out["status"] = out.get("status", "success")
    out["asin"] = asin
    out["model"] = row.model
    return out


@router.post("/evaluate/{asin}")
async def evaluate_purchase(asin: str, session: AsyncSession = Depends(get_session)):
    """对单个 ASIN 做 DeepSeek 采购决策评估（结果落库可追溯）"""
    _require_enabled()
    result = await ai_eval.evaluate_purchase(asin, session)
    if result.get("status") == "failed" and result.get("error") == "产品不存在":
        raise HTTPException(status_code=404, detail="产品不存在")
    return result


@router.post("/daily-report")
async def ai_daily_report(session: AsyncSession = Depends(get_session)):
    """生成采购日报 AI 总结 + 重点提醒全面分析"""
    _require_enabled()
    summary = await get_daily_summary(session, include_results=False)
    return await ai_eval.generate_daily_report_ai(summary, session)
