# -*- coding: utf-8 -*-
"""
SIF 生命周期自动识别模块

数据源优先级：数据库 daily_sales_snapshots（每日快照）优先，SIF MCP 补充
  - 销量增长率：数据库近30天销量 vs 前30天；无库数据时用 SIF ops_get_asin_sales_trend 月度序列
  - 30天销量趋势：数据库 thirty_volume
  - 广告投入变化：SIF ads_get_asin_ad_structure（SP/SB/SBV 活动数）
  - 转化率变化：SIF ops_get_asin_traffic_trend（scoreChangeRatio）
  - 评论增长：SIF market_get_asin_profile（评论数 + 近30天购买量）

输出：生命周期（启动期/增长期/热卖期/成熟期/下降期）+ 采购策略
  启动期 → 小批测试
  增长期 → 逐步增加采购
  热卖期 → 保证不断货
  成熟期 → 稳定补货
  下降期 → 减少采购/不采购
"""
import logging
import json
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_snapshot import DailySalesSnapshot
from app.services.mcp_client import mcp_call

logger = logging.getLogger(__name__)

# 生命周期 → 采购策略
LIFECYCLE_POLICY = {
    "启动期": "小批测试",
    "增长期": "逐步增加采购",
    "热卖期": "保证不断货",
    "成熟期": "稳定补货",
    "下降期": "减少采购/不采购",
}


def _safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    try:
        if v is None:
            return default
        return int(float(v))
    except (ValueError, TypeError):
        return default


# ────────────── SIF 信号获取 ──────────────

def sif_sales_trend(asin: str) -> dict:
    """SIF 月度销量趋势 → {dates: [...], bought: [...]}"""
    r = mcp_call("sif", "ops_get_asin_sales_trend", {"asin": asin})
    dates = r.get("boughtHistoryDates", [])
    bought = []
    for ch in r.get("chars", []):
        # 找到主 ASIN 的序列（通常第一个）
        bl = ch.get("boughtList", [])
        if bl:
            bought = [_safe_int(b.get("bought")) for b in bl]
            break
    return {"dates": dates, "bought": bought}


def sif_asin_profile(asin: str) -> dict:
    """SIF 市场画像 → 评分/评论数/近30天购买量"""
    r = mcp_call("sif", "market_get_asin_profile", {"asins": [asin]})
    lst = r.get("list", []) or []
    if not lst:
        return {}
    item = lst[0]
    return {
        "rating": _safe_float(item.get("star_rating")),
        "review_count": _safe_int(item.get("rating_num")),
        "bought_in_past_month": _safe_int(item.get("bought_in_past_month")),
    }


def sif_ad_structure(asin: str) -> dict:
    """SIF 广告结构 → 各类型广告活动数"""
    try:
        r = mcp_call("sif", "ads_get_asin_ad_structure", {"asin": asin})
    except Exception as e:
        logger.warning("SIF 广告结构获取失败 %s: %s", asin, e)
        return {}
    ad_types = r.get("ad_types", []) or []
    campaigns = 0
    for t in ad_types:
        campaigns += _safe_int(t.get("campaign_count"))
    return {"total_campaigns": campaigns, "ad_types": ad_types}


def sif_traffic_trend(asin: str) -> dict:
    """SIF 流量趋势 → 转化率/流量变化（scoreChangeRatio 均值）"""
    try:
        r = mcp_call("sif", "ops_get_asin_traffic_trend", {"asin": asin})
    except Exception as e:
        logger.warning("SIF 流量趋势获取失败 %s: %s", asin, e)
        return {}
    scores = r.get("totalScore", []) or []
    if not scores:
        return {}
    changes = [_safe_float(s.get("scoreChangeRatio")) for s in scores]
    # 最近4周 vs 前4周
    recent = changes[-4:]
    early = changes[-8:-4]
    avg_recent = sum(recent) / len(recent) if recent else 0.0
    avg_early = sum(early) / len(early) if early else 0.0
    return {"avg_change_recent": avg_recent, "avg_change_early": avg_early}


# ────────────── 数据库信号（优先） ──────────────

async def db_sales_signals(asin: str, session: AsyncSession) -> dict:
    """数据库快照销量信号（优先数据源）"""
    rows = (await session.execute(
        select(DailySalesSnapshot)
        .where(DailySalesSnapshot.asin == asin)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(2)
    )).scalars().all()
    if not rows:
        return {}
    latest = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    growth = None
    if prev and prev.thirty_volume:
        growth = (latest.thirty_volume - prev.thirty_volume) / prev.thirty_volume
    return {
        "thirty_volume": latest.thirty_volume,
        "seven_volume": latest.seven_volume,
        "growth_rate": growth,
        "category_rank": latest.category_rank,
        "snapshot_date": latest.snapshot_date,
    }


# ────────────── 综合识别 ──────────────

def _calc_growth_from_series(bought: list) -> float | None:
    """从 SIF 月度序列计算增长率（最近3月 vs 前3月）"""
    if len(bought) < 4:
        return None
    recent = bought[-3:]
    early = bought[-6:-3]
    avg_recent = sum(recent) / len(recent)
    avg_early = sum(early) / len(early)
    if avg_early <= 0:
        return 1.0 if avg_recent > 0 else 0.0
    return (avg_recent - avg_early) / avg_early


async def identify_with_sif(asin: str, product, session: AsyncSession) -> dict:
    """综合 SIF + 数据库信号识别生命周期，返回 {lifecycle, purchase_policy, signals, reason}"""
    signals = {}

    # 1. 数据库销量信号（优先）
    db_sig = await db_sales_signals(asin, session)
    if db_sig:
        signals["db_thirty_volume"] = db_sig.get("thirty_volume")
        signals["db_growth_rate"] = db_sig.get("growth_rate")
        signals["db_category_rank"] = db_sig.get("category_rank")

    # 2. SIF 销量趋势（库无数据时使用）
    try:
        trend = sif_sales_trend(asin)
        if trend.get("bought"):
            sif_growth = _calc_growth_from_series(trend["bought"])
            signals["sif_growth_rate"] = sif_growth
            signals["sif_last_month_bought"] = trend["bought"][-1] if trend["bought"] else 0
            signals["sif_dates"] = trend["dates"][-3:]
    except Exception as e:
        logger.warning("SIF 销量趋势获取失败 %s: %s", asin, e)

    # 3. SIF 市场画像（评论/评分）
    try:
        profile = sif_asin_profile(asin)
        if profile:
            signals.update(profile)
    except Exception as e:
        logger.warning("SIF 画像获取失败 %s: %s", asin, e)

    # 4. SIF 广告结构
    try:
        ad = sif_ad_structure(asin)
        if ad:
            signals["sif_total_campaigns"] = ad.get("total_campaigns")
    except Exception as e:
        logger.warning("SIF 广告获取失败 %s: %s", asin, e)

    # 5. SIF 流量/转化率趋势
    try:
        traffic = sif_traffic_trend(asin)
        if traffic:
            signals["sif_traffic_recent"] = round(traffic["avg_change_recent"], 4)
            signals["sif_traffic_early"] = round(traffic["avg_change_early"], 4)
    except Exception as e:
        logger.warning("SIF 流量获取失败 %s: %s", asin, e)

    # 综合判定
    lifecycle, reason = _decide_lifecycle(product, signals)

    return {
        "asin": asin,
        "lifecycle": lifecycle,
        "purchase_policy": LIFECYCLE_POLICY.get(lifecycle, "—"),
        "signals": signals,
        "reason": reason,
    }


def _decide_lifecycle(product, signals: dict) -> tuple:
    """规则判定生命周期 → (lifecycle, reason)"""
    # 优先增长率
    growth = signals.get("db_growth_rate")
    if growth is None:
        growth = signals.get("sif_growth_rate")
    thirty = signals.get("db_thirty_volume")
    if thirty is None:
        thirty = signals.get("sif_last_month_bought")
    review = signals.get("review_count")
    campaigns = signals.get("sif_total_campaigns")
    traffic = signals.get("sif_traffic_recent")

    # 新品（上架≤1年）：默认启动期，有销量增长则增长期
    is_new = bool(product.list_date) and (date.today() - product.list_date).days <= 365

    reasons = []

    if growth is not None and growth >= 0.5:
        lifecycle = "增长期"
        reasons.append(f"销量增长率≥50%（{growth:.0%}）")
        if (thirty or 0) > 500:
            lifecycle = "热卖期"
            reasons.append(f"且近30天销量高（{thirty}）")
    elif growth is not None and growth >= 0.2:
        lifecycle = "热卖期"
        reasons.append(f"销量增长率≥20%（{growth:.0%}）")
    elif growth is not None and growth >= -0.2:
        lifecycle = "成熟期"
        reasons.append(f"销量增长率平稳（{growth:.0%}）")
    elif growth is not None and growth >= -0.5:
        lifecycle = "下降期"
        reasons.append(f"销量下降（{growth:.0%}）")
    else:
        if is_new:
            lifecycle = "启动期"
            reasons.append("新品上架≤1年，处于启动期")
        elif growth is None:
            lifecycle = "成熟期"
            reasons.append("无销量数据，按成熟期处理")
        else:
            lifecycle = "下降期"
            reasons.append(f"销量下滑明显（{growth:.0%}）")

    # 信号加强（流量下滑仅在销量未增长时覆盖，避免季节性回落误判）
    if is_new and lifecycle in ("成熟期", "下降期") and growth is not None and growth < 0.2:
        lifecycle = "启动期"
        reasons.append("新品且销量尚未起量")
    if review is not None and review < 50 and lifecycle == "热卖期":
        lifecycle = "增长期"
        reasons.append("评论数少(<50)，尚未达到热卖")
    if campaigns is not None and campaigns == 0 and lifecycle in ("增长期", "热卖期"):
        reasons.append("当前无广告活动")
    if (
        traffic is not None and traffic < -0.3
        and (growth is None or growth < 0.2)
        and lifecycle in ("增长期", "热卖期")
    ):
        lifecycle = "下降期"
        reasons.append("流量/转化率显著下滑")

    if not reasons:
        reasons.append("数据不足，按成熟期处理")
    return lifecycle, "；".join(reasons)
