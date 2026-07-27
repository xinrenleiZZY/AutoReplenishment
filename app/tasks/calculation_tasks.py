"""批量计算调度模块 - 计算编排器"""

import json
import logging
from datetime import date, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.product import Product
from app.models.sales import SalesData
from app.models.inventory import InventorySnapshot
from app.models.seasonal_curve import SeasonalCurve
from app.models.calculation import CalculationResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  工具函数
# ──────────────────────────────────────────────

def _safe_int(val, default=0):
    if val is None:
        return default
    return int(val)


def _safe_float(val, default=0.0):
    if val is None:
        return default
    return float(val)


# ──────────────────────────────────────────────
#  单ASIN完整计算流程
# ──────────────────────────────────────────────

async def run_single_calculation(asin: str, session: AsyncSession) -> dict:
    """对单个ASIN执行完整计算流程，返回结果字典"""
    logger.info("开始计算 ASIN=%s", asin)

    # ── Step 1: 获取产品信息 ──
    result = await session.execute(select(Product).where(Product.asin == asin))
    product: Product | None = result.scalar_one_or_none()
    if product is None:
        logger.error("产品不存在 ASIN=%s", asin)
        return {"asin": asin, "error": "产品不存在", "calc_date": date.today().isoformat()}
    logger.info("Step 1/12 产品信息获取成功 name=%s", product.product_name)

    # ── Step 2: 识别生命周期 ──
    life_cycle = _identify_lifecycle(product)
    logger.info("Step 2/12 生命周期识别 life_cycle=%s", life_cycle)

    # ── Step 3: 分析历史销量 ──
    history = await _analyze_sales_history(asin, session)
    logger.info("Step 3/12 历史销量分析完成 月均=%s", history.get("monthly_avg"))

    # ── Step 4: 获取/计算季节曲线 ──
    seasonal_curve = await _get_seasonal_curve(product, session)
    logger.info("Step 4/12 季节曲线获取完成 festival=%s", seasonal_curve.get("festival"))

    # ── Step 5: 预测未来销量 ──
    forecast = _forecast_sales(product, history, seasonal_curve)
    logger.info("Step 5/12 未来销量预测完成 forecast_total=%s", forecast.get("forecast_total"))

    # ── Step 6: 分析库存健康 ──
    inventory = await _analyze_inventory(asin, session)
    logger.info("Step 6/12 库存健康分析完成 available=%s, inventory_days=%s",
                inventory.get("available_stock"), inventory.get("inventory_days"))

    # ── Step 7: 计算采购触发 ──
    trigger = _calc_purchase_trigger(forecast, inventory, product)
    logger.info("Step 7/12 采购触发判断 purchase_trigger=%s", trigger.get("purchase_trigger"))

    # ── Step 8: 计算建议数量 ──
    suggested_qty = _calc_suggested_qty(forecast, inventory, product)
    logger.info("Step 8/12 建议数量计算 suggested_qty=%s", suggested_qty)

    # ── Step 9: 规划批次 ──
    batch_plan = _plan_batches(suggested_qty, product)
    logger.info("Step 9/12 批次规划完成 batches=%s", len(batch_plan.get("batches", [])))

    # ── Step 10: 计算评分 ──
    scoring = _calc_score(forecast, inventory, life_cycle, trigger, product)
    logger.info("Step 10/12 采购评分 score=%s, level=%s", scoring.get("purchase_score"), scoring.get("purchase_level"))

    # ── Step 11: 存储计算结果 ──
    calc_date = date.today()
    calc_result = CalculationResult(
        asin=asin,
        calc_date=calc_date,
        forecast_months=json.dumps(forecast.get("forecast_months", []), ensure_ascii=False),
        forecast_total=forecast.get("forecast_total"),
        available_stock=inventory.get("available_stock"),
        inventory_days=inventory.get("inventory_days"),
        replenishment_cycle=inventory.get("replenishment_cycle"),
        urgency_score=inventory.get("urgency_score"),
        purchase_trigger=trigger.get("purchase_trigger"),
        suggested_qty=suggested_qty,
        batch_plan=json.dumps(batch_plan, ensure_ascii=False),
        purchase_score=scoring.get("purchase_score"),
        purchase_level=scoring.get("purchase_level"),
        score_detail=json.dumps(scoring.get("score_detail", {}), ensure_ascii=False),
    )
    session.add(calc_result)
    await session.flush()
    logger.info("Step 11/12 计算结果已存储 id=%s", calc_result.id)

    # ── Step 12: 返回结果 ──
    result_dict = {
        "id": calc_result.id,
        "asin": asin,
        "product_name": product.product_name,
        "calc_date": calc_date.isoformat(),
        "life_cycle": life_cycle,
        "forecast_total": forecast.get("forecast_total"),
        "forecast_months": forecast.get("forecast_months"),
        "available_stock": inventory.get("available_stock"),
        "inventory_days": inventory.get("inventory_days"),
        "replenishment_cycle": inventory.get("replenishment_cycle"),
        "urgency_score": inventory.get("urgency_score"),
        "purchase_trigger": trigger.get("purchase_trigger"),
        "suggested_qty": suggested_qty,
        "batch_plan": batch_plan,
        "purchase_score": scoring.get("purchase_score"),
        "purchase_level": scoring.get("purchase_level"),
        "score_detail": scoring.get("score_detail"),
        "reason": trigger.get("reason", ""),
    }
    logger.info("Step 12/12 计算完成 ASIN=%s score=%s level=%s", asin, scoring.get("purchase_score"), scoring.get("purchase_level"))
    return result_dict


# ──────────────────────────────────────────────
#  批量计算
# ──────────────────────────────────────────────

async def run_batch_calculation():
    """对所有启用产品执行批量计算"""
    logger.info("===== 批量计算任务开始 =====")
    session = async_session_factory()
    try:
        async with session:
            # 获取所有启用产品
            result = await session.execute(
                select(Product).where(Product.status == True)  # noqa: E712
            )
            products = result.scalars().all()
            logger.info("共获取 %d 个启用产品", len(products))

            stats = {
                "total": len(products),
                "success": 0,
                "failed": 0,
                "immediate": 0,
                "observe": 0,
                "pause": 0,
                "errors": [],
            }

            for product in products:
                try:
                    calc_result = await run_single_calculation(product.asin, session)
                    if "error" in calc_result:
                        stats["failed"] += 1
                        stats["errors"].append({"asin": product.asin, "error": calc_result["error"]})
                    else:
                        stats["success"] += 1
                        level = calc_result.get("purchase_level", "")
                        if level == "立即采购":
                            stats["immediate"] += 1
                        elif level == "观察":
                            stats["observe"] += 1
                        elif level == "暂停":
                            stats["pause"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    stats["errors"].append({"asin": product.asin, "error": str(e)})
                    logger.error("计算失败 ASIN=%s error=%s", product.asin, e)

            await session.commit()
            logger.info("===== 批量计算任务完成: 总计=%d, 成功=%d, 失败=%d, 立即采购=%d, 观察=%d, 暂停=%d =====",
                        stats["total"], stats["success"], stats["failed"],
                        stats["immediate"], stats["observe"], stats["pause"])
            return stats

    except Exception as e:
        logger.error("批量计算异常: %s", e)
        return {"total": 0, "success": 0, "failed": 0, "immediate": 0, "observe": 0, "pause": 0, "errors": [str(e)]}
    finally:
        await session.close()


# ──────────────────────────────────────────────
#  日报汇总
# ──────────────────────────────────────────────

async def get_daily_summary(session: AsyncSession) -> dict:
    """生成日报汇总数据"""
    today = date.today()

    # 当日所有计算结果
    result = await session.execute(
        select(CalculationResult).where(CalculationResult.calc_date == today)
    )
    all_results = result.scalars().all()

    total_asins = len(all_results)

    # 按采购级别统计
    immediate = [r for r in all_results if r.purchase_level == "立即采购"]
    observe = [r for r in all_results if r.purchase_level == "观察"]
    pause = [r for r in all_results if r.purchase_level == "暂停"]

    # 重点提醒列表（按评分降序取前10个立即采购的）
    immediate_sorted = sorted(immediate, key=lambda r: _safe_float(r.purchase_score), reverse=True)
    top_alerts = []
    for r in immediate_sorted[:10]:
        product_name = await _get_product_name(r.asin, session)
        top_alerts.append({
            "asin": r.asin,
            "product_name": product_name,
            "purchase_score": r.purchase_score,
            "purchase_level": r.purchase_level,
            "suggested_qty": r.suggested_qty,
            "inventory_days": r.inventory_days,
            "purchase_trigger": r.purchase_trigger,
        })

    return {
        "calc_date": today.isoformat(),
        "total_asins": total_asins,
        "immediate_count": len(immediate),
        "observe_count": len(observe),
        "pause_count": len(pause),
        "top_alerts": top_alerts,
        "results": [
            {
                "asin": r.asin,
                "purchase_level": r.purchase_level,
                "purchase_score": r.purchase_score,
                "suggested_qty": r.suggested_qty,
                "inventory_days": r.inventory_days,
            }
            for r in all_results
        ],
    }


# ──────────────────────────────────────────────
#  内部实现函数
# ──────────────────────────────────────────────

def _identify_lifecycle(product: Product) -> str:
    """识别产品生命周期阶段"""
    if product.life_cycle:
        return product.life_cycle

    if product.list_date is None:
        return "未知"

    days_since_list = (date.today() - product.list_date).days

    if days_since_list <= 90:
        return "新品期"
    elif days_since_list <= 180:
        return "成长期"
    elif days_since_list <= 730:
        return "成熟期"
    else:
        return "衰退期"


async def _analyze_sales_history(asin: str, session: AsyncSession) -> dict:
    """分析历史销量数据"""
    today = date.today()
    one_year_ago = today - timedelta(days=365)

    result = await session.execute(
        select(SalesData)
        .where(SalesData.asin == asin, SalesData.date >= one_year_ago)
        .order_by(SalesData.date)
    )
    records = result.scalars().all()

    if not records:
        return {
            "total": 0,
            "monthly_avg": 0,
            "monthly_data": {},
            "last_year_same_month": 0,
            "recent_3_months_avg": 0,
        }

    total = sum(r.sales_qty for r in records)
    months_covered = max(1, (records[-1].date - records[0].date).days / 30)
    monthly_avg = round(total / months_covered) if months_covered > 0 else 0

    # 按月分组
    monthly_data = {}
    for r in records:
        key = f"{r.date.year}-{r.date.month:02d}"
        monthly_data[key] = monthly_data.get(key, 0) + r.sales_qty

    # 去年同月销量
    last_year_month = f"{today.year - 1}-{today.month:02d}"
    last_year_same_month = monthly_data.get(last_year_month, 0)

    # 近3个月平均
    recent_3 = {}
    for i in range(1, 4):
        m = today.month - i
        y = today.year
        if m <= 0:
            m += 12
            y -= 1
        key = f"{y}-{m:02d}"
        if key in monthly_data:
            recent_3[key] = monthly_data[key]
    recent_3_months_avg = round(sum(recent_3.values()) / max(len(recent_3), 1)) if recent_3 else monthly_avg

    return {
        "total": total,
        "monthly_avg": monthly_avg,
        "monthly_data": monthly_data,
        "last_year_same_month": last_year_same_month,
        "recent_3_months_avg": recent_3_months_avg,
    }


async def _get_seasonal_curve(product: Product, session: AsyncSession) -> dict:
    """获取季节曲线"""
    festival = product.festival or ""
    sub_category = product.sub_category or ""

    if festival and sub_category:
        result = await session.execute(
            select(SeasonalCurve).where(
                SeasonalCurve.festival == festival,
                SeasonalCurve.sub_category == sub_category,
            )
        )
        curve = result.scalar_one_or_none()
        if curve and curve.month_distribution:
            return {
                "festival": festival,
                "sub_category": sub_category,
                "distribution": json.loads(curve.month_distribution),
                "source": "db",
            }

    # 无季节曲线，返回默认
    return {
        "festival": festival,
        "sub_category": sub_category,
        "distribution": {f"{i:02d}": round(100 / 12, 1) for i in range(1, 13)},
        "source": "default",
    }


def _forecast_sales(product: Product, history: dict, seasonal_curve: dict) -> dict:
    """预测未来销量"""
    forecast_months = []
    forecast_total = 0
    today = date.today()

    is_new = (product.product_stage or "").lower() in ("新品", "new")
    monthly_avg = history.get("monthly_avg", 0)
    last_year_same_month = history.get("last_year_same_month", 0)

    for i in range(1, 7):  # 预测未来6个月
        forecast_month = today.month + i
        forecast_year = today.year
        if forecast_month > 12:
            forecast_month -= 12
            forecast_year += 1

        month_key = f"{forecast_month:02d}"
        month_label = f"{forecast_year}-{forecast_month:02d}"

        # 季节系数
        dist = seasonal_curve.get("distribution", {})
        seasonal_factor = float(dist.get(month_key, 100)) / 100.0

        if is_new:
            # 新品：季节曲线占比 × 年预测总量
            annual_forecast = monthly_avg * 12 if monthly_avg > 0 else 100
            month_forecast = round(annual_forecast * seasonal_factor / 12)
        else:
            # 老品：去年同月销量 × 趋势系数(25%) ... 简化用最近3月平均
            base = last_year_same_month if last_year_same_month > 0 else monthly_avg
            trend_factor = 1.0  # 可配
            month_forecast = round(base * trend_factor * seasonal_factor)

        forecast_months.append({
            "month": month_label,
            "forecast_qty": month_forecast,
            "seasonal_factor": round(seasonal_factor, 3),
        })
        forecast_total += month_forecast

    return {
        "forecast_months": forecast_months,
        "forecast_total": forecast_total,
    }


async def _analyze_inventory(asin: str, session: AsyncSession) -> dict:
    """分析库存健康"""
    today = date.today()

    result = await session.execute(
        select(InventorySnapshot)
        .where(InventorySnapshot.asin == asin)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(1)
    )
    snap = result.scalar_one_or_none()

    if snap is None:
        return {
            "available_stock": 0,
            "inventory_days": 0,
            "replenishment_cycle": 30,
            "urgency_score": 0,
        }

    available = (
        _safe_int(snap.fba_available)
        + _safe_int(snap.local_stock)
        + _safe_int(snap.fba_inbound)
        + _safe_int(snap.fba_inbound_shipped)
        + _safe_int(snap.purchase_on_order)
    )
    fba_only = _safe_int(snap.fba_available)

    return {
        "available_stock": available,
        "fba_available": fba_only,
        "inventory_days": 0,  # 在触发判断里计算
        "replenishment_cycle": 30,
        "urgency_score": 0,  # 在评分里计算
        "fba_reserved": _safe_int(snap.fba_reserved),
        "fba_inbound": _safe_int(snap.fba_inbound),
        "local_stock": _safe_int(snap.local_stock),
        "purchase_on_order": _safe_int(snap.purchase_on_order),
    }


def _calc_purchase_trigger(forecast: dict, inventory: dict, product: Product) -> dict:
    """计算采购触发判断"""
    forecast_total = forecast.get("forecast_total", 0)
    available_stock = inventory.get("available_stock", 0)

    # 日均销量估算（按未来6个月总预测 / 180天）
    daily_sales = forecast_total / 180 if forecast_total > 0 else 0
    inventory_days = round(available_stock / daily_sales) if daily_sales > 0 else 999

    # 更新库存覆盖天数
    inventory["inventory_days"] = inventory_days

    # 补货周期（海运+生产）
    lead_time = _safe_int(product.lead_time, 30)
    replenishment_cycle = lead_time + 30  # 30天海运

    # 触发条件：库存覆盖天数 < 补货周期
    if inventory_days < replenishment_cycle:
        return {
            "purchase_trigger": "需要采购",
            "reason": f"库存仅覆盖{inventory_days}天，低于补货周期{replenishment_cycle}天",
            "inventory_days": inventory_days,
            "replenishment_cycle": replenishment_cycle,
        }
    else:
        return {
            "purchase_trigger": "无需采购",
            "reason": f"库存可覆盖{inventory_days}天，高于补货周期{replenishment_cycle}天",
            "inventory_days": inventory_days,
            "replenishment_cycle": replenishment_cycle,
        }


def _calc_suggested_qty(forecast: dict, inventory: dict, product: Product) -> int:
    """计算建议采购数量"""
    forecast_total = forecast.get("forecast_total", 0)
    available_stock = inventory.get("available_stock", 0)
    box_qty = _safe_int(product.box_quantity, 1)
    min_order = _safe_int(product.min_order_qty, 1)

    # 建议数量 = 未来N月预测需求 + 安全库存 - 当前可用库存
    safe_stock = round(forecast_total / 6)  # 1个月安全库存
    suggested = forecast_total + safe_stock - available_stock

    if suggested <= 0:
        return 0

    # 向上取整至装箱数量倍数
    suggested = ceil(suggested / box_qty) * box_qty

    # 不低于最低采购量
    if suggested < min_order:
        suggested = min_order

    return suggested


def _plan_batches(suggested_qty: int, product: Product) -> dict:
    """规划采购批次"""
    if suggested_qty <= 0:
        return {"batches": [], "total_qty": 0}

    lead_time = _safe_int(product.lead_time, 30)
    box_qty = _safe_int(product.box_quantity, 1)

    # 简单分批：第一批走空运（急），第二批走海运（不急）
    if suggested_qty <= box_qty * 2:
        batches = [
            {"batch_no": 1, "qty": suggested_qty, "method": "空运", "days": lead_time + 10},
        ]
    else:
        first_batch = ceil(suggested_qty * 0.3 / box_qty) * box_qty
        second_batch = suggested_qty - first_batch
        batches = [
            {"batch_no": 1, "qty": first_batch, "method": "空运", "days": lead_time + 10},
            {"batch_no": 2, "qty": second_batch, "method": "海运", "days": lead_time + 45},
        ]

    return {
        "batches": batches,
        "total_qty": suggested_qty,
    }


def _calc_score(forecast: dict, inventory: dict, life_cycle: str,
                trigger: dict, product: Product) -> dict:
    """计算采购评分"""
    # 断货风险（25%）
    inventory_days = trigger.get("inventory_days", 30)
    replenishment_cycle = trigger.get("replenishment_cycle", 60)
    if inventory_days <= 0:
        shortage_score = 100
    elif inventory_days >= replenishment_cycle:
        shortage_score = 0
    else:
        shortage_score = max(0, round((1 - inventory_days / replenishment_cycle) * 100))

    # 销量趋势（25%）
    recent_3 = forecast.get("forecast_total", 0)
    trend_score = min(100, round(recent_3 / 10)) if recent_3 > 0 else 10

    # 利润空间（20%）
    profit_rate = _safe_float(product.profit_rate, 0.15)
    profit_score = min(100, round(profit_rate * 100))

    # 生命周期（10%）
    life_cycle_scores = {
        "新品期": 90, "成长期": 80, "成熟期": 60, "衰退期": 20, "未知": 50,
    }
    life_score = life_cycle_scores.get(life_cycle, 50)

    # 库存紧急（10%）
    urgency = max(0, min(100, round((1 - inventory_days / max(replenishment_cycle, 1)) * 100)))

    # 运输可达（10%）- 默认70分
    transport_score = 70

    # 总分
    total_score = round(
        shortage_score * 0.25
        + trend_score * 0.25
        + profit_score * 0.20
        + life_score * 0.10
        + urgency * 0.10
        + transport_score * 0.10
    )

    # 采购级别
    if total_score >= 80:
        level = "立即采购"
    elif total_score >= 60:
        level = "观察"
    else:
        level = "暂停"

    inventory["urgency_score"] = urgency

    return {
        "purchase_score": total_score,
        "purchase_level": level,
        "score_detail": {
            "shortage_score": {"value": shortage_score, "weight": 0.25, "label": "断货风险"},
            "trend_score": {"value": trend_score, "weight": 0.25, "label": "销量趋势"},
            "profit_score": {"value": profit_score, "weight": 0.20, "label": "利润空间"},
            "life_score": {"value": life_score, "weight": 0.10, "label": "生命周期"},
            "urgency": {"value": urgency, "weight": 0.10, "label": "库存紧急"},
            "transport_score": {"value": transport_score, "weight": 0.10, "label": "运输可达"},
        },
    }


async def _get_product_name(asin: str, session: AsyncSession) -> str:
    """根据ASIN获取产品名称"""
    result = await session.execute(
        select(Product.product_name).where(Product.asin == asin)
    )
    row = result.scalar_one_or_none()
    return row or ""
