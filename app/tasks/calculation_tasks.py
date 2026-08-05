"""批量计算调度模块 - 计算编排器（支持按等级频率计算）"""

import json
import logging
from datetime import date, timedelta
from math import ceil

from sqlalchemy import func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.product import Product
from app.models.sales import SalesData
from app.models.inventory import InventorySnapshot
from app.models.seasonal_curve import SeasonalCurve
from app.models.category_leadtime import CategoryLeadtime
from app.models.calculation import CalculationResult, CalculationStepResult
from app.services.time_axis import get_sales_phase, check_purchase_window, get_recommended_transport, get_festival_info
from app.services.forecast import forecast_all_months
from app.services import new_product_policy

logger = logging.getLogger(__name__)

# P0-P4 与产品等级 S/A/B/C/D 的对应关系（calc_frequency 字段历史值）
_P_LEVEL_MAP = {"P0": "S", "P1": "A", "P2": "B", "P3": "C", "P4": "D"}
_FREQ_CACHE: dict | None = None


def _parse_frequencies(raw: str) -> dict:
    """解析 CALC_FREQUENCIES（格式: S:1,A:3,B:5,C:7,D:14）"""
    global _FREQ_CACHE
    if _FREQ_CACHE is None:
        parsed = {}
        for part in (raw or "").split(","):
            part = part.strip()
            if not part or ":" not in part:
                continue
            key, _, val = part.partition(":")
            try:
                parsed[key.strip().upper()] = int(val.strip())
            except ValueError:
                continue
        _FREQ_CACHE = parsed
    return _FREQ_CACHE


def get_product_level(product: Product) -> str:
    """获取产品等级（S/A/B/C/D），优先 calc_frequency(P0-P4)，其次 product_level"""
    if product.calc_frequency and product.calc_frequency.upper() in _P_LEVEL_MAP:
        return _P_LEVEL_MAP[product.calc_frequency.upper()]
    return (product.product_level or "").upper() or "未知"


def get_calculation_frequency_days(product: Product) -> int:
    """获取产品计算频率（天）

    优先级：
    1) calc_frequency 直接写数字（如 "2"）时作为单产品覆盖；
    2) 按等级 S/A/B/C/D 读取 CALC_FREQUENCIES 配置（可自定义）；
    3) 未匹配时使用 CALC_FREQUENCY_DEFAULT。
    """
    if product.calc_frequency and product.calc_frequency.strip().isdigit():
        return int(product.calc_frequency.strip())
    freq_map = _parse_frequencies(settings.CALC_FREQUENCIES)
    level = get_product_level(product)
    return freq_map.get(level, settings.CALC_FREQUENCY_DEFAULT)


def is_calculation_due(last_calc_date: date | None, today: date, frequency_days: int) -> bool:
    """判断产品是否到期：无历史结果，或距上次计算天数 >= 频率天数"""
    if last_calc_date is None:
        return True
    return (today - last_calc_date).days >= frequency_days


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


def _to_json(obj) -> str | None:
    """对象转JSON字符串，失败返回None"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return None


def _parse_step_json(s: str | None):
    """JSON字符串转对象，用于API返回展示"""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


class StepRecorder:
    """计算步骤记录器 — 记录每一步的输入/输出/原因，实现可溯源"""

    def __init__(self, session: AsyncSession, asin: str, calc_date: date):
        self.session = session
        self.asin = asin
        self.calc_date = calc_date
        self.steps = []  # 内存暂存，等 calculation_id 确定后批量入库

    def record(self, step_no: int, step_name: str, output: dict,
               reason: str = "", status: str = "success", input_data: dict = None):
        """记录一个步骤（暂存内存，待计算ID确定后保存）"""
        self.steps.append({
            "step_no": step_no,
            "step_name": step_name,
            "status": status,
            "input_data": _to_json(input_data),
            "output_data": _to_json(output),
            "output": output,       # 原始对象，用于API返回展示
            "reason": reason,
        })
        logger.info("Step %s/12 %s %s", step_no, step_name, status)

    def save(self, calculation_id: int):
        """将步骤写入数据库（需在计算结果持久化后调用）"""
        for s in self.steps:
            self.session.add(CalculationStepResult(
                calculation_id=calculation_id,
                asin=self.asin,
                calc_date=self.calc_date,
                step_no=s["step_no"],
                step_name=s["step_name"],
                status=s["status"],
                input_data=s["input_data"],
                output_data=s["output_data"],
                reason=s["reason"],
            ))


# ──────────────────────────────────────────────
#  单ASIN完整计算流程
# ──────────────────────────────────────────────

async def run_single_calculation(asin: str, session: AsyncSession) -> dict:
    """对单个ASIN执行完整计算流程，返回结果字典（含每步溯源）"""
    logger.info("开始计算 ASIN=%s", asin)
    calc_date = date.today()
    recorder = StepRecorder(session, asin, calc_date)

    # 当日重算覆盖：清理同一天旧结果及其步骤，保证可重复触发
    existing = await session.execute(
        select(CalculationResult).where(
            CalculationResult.asin == asin,
            CalculationResult.calc_date == calc_date,
        )
    )
    old = existing.scalar_one_or_none()
    if old is not None:
        await session.execute(
            delete(CalculationStepResult).where(CalculationStepResult.calculation_id == old.id)
        )
        await session.delete(old)
        await session.flush()
        logger.info("已清理当日旧结果 id=%s（重算覆盖）", old.id)

    # ── Step 1: 获取产品信息 ──
    result = await session.execute(select(Product).where(Product.asin == asin))
    product: Product | None = result.scalar_one_or_none()
    if product is None:
        logger.error("产品不存在 ASIN=%s", asin)
        return {"asin": asin, "error": "产品不存在", "calc_date": calc_date.isoformat()}
    recorder.record(1, "产品信息获取", {
        "asin": asin,
        "product_name": product.product_name,
        "category": product.category,
        "festival": product.festival,
        "product_stage": product.product_stage,
        "product_level": product.product_level,
        "lead_time": product.lead_time,
        "list_date": str(product.list_date) if product.list_date else None,
    }, input_data={"asin": asin}, reason=f"产品档案: {product.product_name}")

    # ── Step 2: 识别生命周期 ──
    life_cycle = _identify_lifecycle(product)
    recorder.record(2, "生命周期识别", {
        "life_cycle": life_cycle,
        "product_stage": product.product_stage,
        "list_date": str(product.list_date) if product.list_date else None,
    }, input_data={
        "list_date": str(product.list_date) if product.list_date else None,
        "product_stage": product.product_stage,
        "product_level": product.product_level,
        "festival": product.festival,
    }, reason=f"生命周期: {life_cycle}")

    # ── Step 2.5: 销售时间轴判断 ──
    sales_phase_info = await get_sales_phase(product, session)
    purchase_window = await check_purchase_window(product, session)
    recorder.record(3, "销售时间轴判断", {
        "phase": sales_phase_info.get("phase"),
        "days_to_festival": sales_phase_info.get("days_to_festival"),
        "is_in_season": sales_phase_info.get("is_in_season"),
        "can_purchase": purchase_window.get("can_purchase"),
        "recommended_transport": purchase_window.get("recommended_transport"),
        "latest_purchase_date": str(purchase_window.get("latest_purchase_date")) if purchase_window.get("latest_purchase_date") else None,
    }, input_data={
        "today": calc_date.isoformat(),
        "festival": product.festival,
        "category": product.category,
        "lead_time": product.lead_time,
    }, reason=f"{sales_phase_info.get('reason', '')} | {purchase_window.get('reason', '')}")

    # ── Step 3: 分析历史销量 ──
    history = await _analyze_sales_history(asin, session)
    recorder.record(4, "历史销量分析", {
        "monthly_avg": history.get("monthly_avg"),
        "last_year_same_month": history.get("last_year_same_month"),
        "total_volume": history.get("total_volume"),
        "trend": history.get("trend"),
    }, input_data={"asin": asin}, reason=f"月均销量={history.get('monthly_avg')}, 去年同月={history.get('last_year_same_month')}")

    # ── Step 4: 获取/计算季节曲线 ──
    seasonal_curve = await _get_seasonal_curve(product, session)
    recorder.record(5, "季节曲线获取", {
        "festival": seasonal_curve.get("festival"),
        "sub_category": seasonal_curve.get("sub_category"),
        "source": seasonal_curve.get("source"),
        "distribution": seasonal_curve.get("distribution"),
    }, input_data={
        "festival": product.festival,
        "category": product.category,
    }, reason=f"季节曲线来源: {seasonal_curve.get('source')}")

    # ── Step 5: 预测未来销量（forecast.py 老品/新品模型） ──
    forecast = await _forecast_sales(product, history, seasonal_curve, session)
    recorder.record(6, "未来销量预测", {
        "forecast_total": forecast.get("forecast_total"),
        "forecast_months": forecast.get("forecast_months"),
        "forecast_model": forecast.get("forecast_model"),
    }, input_data={
        "monthly_avg": history.get("monthly_avg"),
        "last_year_same_month": history.get("last_year_same_month"),
        "trend": history.get("trend"),
        "seasonal_distribution": seasonal_curve.get("distribution"),
        "product_stage": product.product_stage,
    }, reason=f"未来{settings.FORECAST_MONTHS}个月预测总销量={forecast.get('forecast_total')}（模型: {forecast.get('forecast_model', 'legacy')}）")

    # ── Step 6: 分析库存健康 ──
    inventory = await _analyze_inventory(asin, session)
    recorder.record(7, "库存健康分析", {
        "available_stock": inventory.get("available_stock"),
        "fba_available": inventory.get("fba_available"),
        "inventory_days": inventory.get("inventory_days"),
        "replenishment_cycle": inventory.get("replenishment_cycle"),
    }, input_data={"asin": asin}, reason=f"可用库存={inventory.get('available_stock')}, 覆盖天数={inventory.get('inventory_days')}")

    # 解析大货工期：优先产品实际填写，否则按分类工期表(category_leadtimes)匹配
    lead_time = await _resolve_lead_time(product, session)

    # ── Step 7: 计算采购触发（结合时间轴） ──
    if not purchase_window.get("can_purchase", True):
        trigger = {
            "purchase_trigger": "禁止采购",
            "reason": purchase_window.get("reason", "无法赶上销售窗口"),
            "inventory_days": inventory.get("inventory_days", 0),
            "replenishment_cycle": inventory.get("replenishment_cycle", 30),
        }
    else:
        trigger = _calc_purchase_trigger(forecast, inventory, product, lead_time)
        # 补充时间轴原因
        trigger["reason"] += f" | {sales_phase_info.get('reason', '')}"
    recorder.record(8, "采购触发判断", {
        "purchase_trigger": trigger.get("purchase_trigger"),
        "inventory_days": trigger.get("inventory_days"),
        "replenishment_cycle": trigger.get("replenishment_cycle"),
        "transport_cycles": trigger.get("transport_cycles"),
        "recommended_transport": trigger.get("recommended_transport"),
    }, input_data={
        "can_purchase": purchase_window.get("can_purchase"),
        "forecast_total": forecast.get("forecast_total"),
        "available_stock": inventory.get("available_stock"),
        "inventory_days": inventory.get("inventory_days"),
        "replenishment_cycle": inventory.get("replenishment_cycle"),
        "lead_time": lead_time,
    }, reason=trigger.get("reason", ""))

    # ── Step 8: 计算建议数量（接入 new_product_policy 节日策略） ──
    suggested_qty, suggest_note = await _calc_suggested_qty_v2(
        forecast, inventory, product, lead_time, life_cycle, session
    )
    recorder.record(9, "建议采购数量计算", {
        "suggested_qty": suggested_qty,
        "forecast_total": forecast.get("forecast_total"),
        "available_stock": inventory.get("available_stock"),
        "strategy_note": suggest_note,
    }, input_data={
        "forecast_total": forecast.get("forecast_total"),
        "forecast_months": forecast.get("forecast_months"),
        "available_stock": inventory.get("available_stock"),
        "inventory_days": inventory.get("inventory_days"),
        "replenishment_cycle": inventory.get("replenishment_cycle"),
        "festival": product.festival,
    }, reason=suggest_note or f"建议数量={suggested_qty}（未来3个月需求-当前库存，向上取整至箱规倍数）")

    # ── Step 9: 规划批次 ──
    batch_plan = _plan_batches(suggested_qty, product, lead_time)
    recorder.record(10, "采购批次规划", batch_plan,
                    input_data={"suggested_qty": suggested_qty},
                    reason=f"拆分为{len(batch_plan.get('batches', []))}批次")

    # ── Step 10: 计算评分（结合时间轴） ──
    scoring = _calc_score(forecast, inventory, life_cycle, trigger, product, purchase_window)
    recorder.record(11, "采购评分模型", {
        "purchase_score": scoring.get("purchase_score"),
        "purchase_level": scoring.get("purchase_level"),
        "score_detail": scoring.get("score_detail"),
    }, input_data={
        "life_cycle": life_cycle,
        "forecast_total": forecast.get("forecast_total"),
        "inventory_days": inventory.get("inventory_days"),
        "urgency_score": inventory.get("urgency_score"),
        "purchase_trigger": trigger.get("purchase_trigger"),
        "sales_phase": sales_phase_info.get("phase"),
        "can_purchase": purchase_window.get("can_purchase"),
    }, reason=f"采购评分={scoring.get('purchase_score')}, 等级={scoring.get('purchase_level')}")

    # ── Step 11: 存储计算结果 ──
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

    # 将步骤记录持久化（绑定 calculation_id）
    recorder.save(calc_result.id)

    logger.info("Step 11/12 计算结果已存储 id=%s", calc_result.id)

    # ── Step 12: 返回结果（含每步溯源） ──
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
        "steps": [
            {
                "step_no": s["step_no"],
                "step_name": s["step_name"],
                "status": s["status"],
                "input": _parse_step_json(s["input_data"]),
                "output": s["output"],
                "reason": s["reason"],
            }
            for s in recorder.steps
        ],  # 每步溯源明细
    }
    logger.info("Step 12/12 计算完成 ASIN=%s score=%s level=%s", asin, scoring.get("purchase_score"), scoring.get("purchase_level"))
    return result_dict


# ──────────────────────────────────────────────
#  批量计算
# ──────────────────────────────────────────────

async def run_due_calculation(force: bool = False) -> dict:
    """按等级频率计算到期产品（S 每天、A 每3天、B 每5天、C 每7天、D 每14天，可自定义）

    force=True 时忽略频率，全量重算所有启用产品。
    """
    mode = "全量" if force else "按频率"
    logger.info("===== %s计算任务开始 =====", mode)
    session = async_session_factory()
    try:
        async with session:
            # 获取所有启用产品
            result = await session.execute(
                select(Product).where(Product.status == True)  # noqa: E712
            )
            products = result.scalars().all()
            logger.info("共获取 %d 个启用产品", len(products))

            # 每个 ASIN 最近一次计算日期（force 模式不需要）
            last_dates: dict[str, date] = {}
            if not force:
                rows = await session.execute(
                    select(CalculationResult.asin, func.max(CalculationResult.calc_date))
                    .group_by(CalculationResult.asin)
                )
                last_dates = {asin: d for asin, d in rows.all()}

            today = date.today()
            stats = {
                "total": len(products),
                "due": 0,
                "skipped": 0,
                "success": 0,
                "failed": 0,
                "immediate": 0,
                "observe": 0,
                "pause": 0,
                "by_level": {},
                "errors": [],
            }

            for product in products:
                level = get_product_level(product)
                if level not in stats["by_level"]:
                    stats["by_level"][level] = {
                        "total": 0,
                        "due": 0,
                        "success": 0,
                        "failed": 0,
                        "frequency_days": get_calculation_frequency_days(product),
                    }
                stats["by_level"][level]["total"] += 1

                if force:
                    due = True
                else:
                    freq = get_calculation_frequency_days(product)
                    last = last_dates.get(product.asin)
                    due = is_calculation_due(last, today, freq)

                if not due:
                    stats["skipped"] += 1
                    continue

                stats["due"] += 1
                stats["by_level"][level]["due"] += 1
                try:
                    calc_result = await run_single_calculation(product.asin, session)
                    if "error" in calc_result:
                        stats["failed"] += 1
                        stats["by_level"][level]["failed"] += 1
                        stats["errors"].append({"asin": product.asin, "error": calc_result["error"]})
                    else:
                        stats["success"] += 1
                        stats["by_level"][level]["success"] += 1
                        purchase_level = calc_result.get("purchase_level", "")
                        if purchase_level == "立即采购":
                            stats["immediate"] += 1
                        elif purchase_level == "观察":
                            stats["observe"] += 1
                        elif purchase_level == "暂停":
                            stats["pause"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    stats["by_level"][level]["failed"] += 1
                    stats["errors"].append({"asin": product.asin, "error": str(e)})
                    logger.error("计算失败 ASIN=%s error=%s", product.asin, e)

            await session.commit()
            logger.info(
                "===== %s计算任务完成: 总计=%d, 到期=%d, 跳过=%d, 成功=%d, 失败=%d, "
                "立即采购=%d, 观察=%d, 暂停=%d =====",
                mode, stats["total"], stats["due"], stats["skipped"],
                stats["success"], stats["failed"],
                stats["immediate"], stats["observe"], stats["pause"],
            )
            return stats

    except Exception as e:
        logger.error("%s计算异常: %s", mode, e)
        return {
            "total": 0, "due": 0, "skipped": 0, "success": 0, "failed": 0,
            "immediate": 0, "observe": 0, "pause": 0, "by_level": {},
            "errors": [str(e)],
        }
    finally:
        await session.close()


async def run_batch_calculation():
    """对所有启用产品执行全量批量计算（等价 run_due_calculation(force=True)）"""
    return await run_due_calculation(force=True)


async def get_due_calculation_stats(session: AsyncSession) -> dict:
    """统计各等级产品数量与今日到期情况（不执行计算，用于预览/接口）"""
    result = await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )
    products = result.scalars().all()
    rows = await session.execute(
        select(CalculationResult.asin, func.max(CalculationResult.calc_date))
        .group_by(CalculationResult.asin)
    )
    last_dates = {asin: d for asin, d in rows.all()}
    today = date.today()

    by_level: dict[str, dict] = {}
    for product in products:
        level = get_product_level(product)
        freq = get_calculation_frequency_days(product)
        if level not in by_level:
            by_level[level] = {"total": 0, "due": 0, "frequency_days": freq}
        by_level[level]["total"] += 1
        last = last_dates.get(product.asin)
        if is_calculation_due(last, today, freq):
            by_level[level]["due"] += 1

    return {
        "date": today.isoformat(),
        "total": len(products),
        "due": sum(v["due"] for v in by_level.values()),
        "by_level": by_level,
    }


# ──────────────────────────────────────────────
#  日报汇总
# ──────────────────────────────────────────────

async def get_daily_summary(session: AsyncSession, include_results: bool = True) -> dict:
    """生成日报汇总数据（include_results=False 时不返回全量明细，用于看板轻量加载）"""
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

    summary = {
        "calc_date": today.isoformat(),
        "total_asins": total_asins,
        "immediate_count": len(immediate),
        "observe_count": len(observe),
        "pause_count": len(pause),
        "top_alerts": top_alerts,
    }
    if include_results:
        summary["results"] = [
            {
                "asin": r.asin,
                "purchase_level": r.purchase_level,
                "purchase_score": r.purchase_score,
                "suggested_qty": r.suggested_qty,
                "inventory_days": r.inventory_days,
            }
            for r in all_results
        ]
    return summary


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


def _forecast_sales_legacy(product: Product, history: dict, seasonal_curve: dict) -> dict:
    """旧简化预测模型（作为 forecast.py 无数据时的兜底）"""
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


async def _forecast_sales(product: Product, history: dict, seasonal_curve: dict, session: AsyncSession) -> dict:
    """预测未来销量（主流程整合 v3：接入 forecast.py 老品/新品预测模型）

    老品: old_product_forecast（历史40% + 趋势25% + 市场15% + 广告10% + Listing10%）
    新品: new_product_forecast（季节曲线占比 × 年预测总量 × 趋势/广告/Listing系数）
    无历史数据或调用失败时回退旧简化模型 _forecast_sales_legacy。
    """
    is_new = (product.product_stage or "").lower() in ("新品", "new")
    try:
        result = await forecast_all_months(
            asin=product.asin,
            forecast_months=settings.FORECAST_MONTHS,
            session=session,
            is_new_product=is_new,
        )
        forecast_total = result.get("total", 0) or 0
        if forecast_total > 0 and result.get("monthly"):
            forecast_months = []
            for m in result["monthly"]:
                month_label = m["month"][:7]  # "2026-09-01" -> "2026-09"
                forecast_months.append({
                    "month": month_label,
                    "forecast_qty": m["forecast"],
                    "seasonal_factor": 1.0,  # 新模型内部已含季节占比
                })
            return {
                "forecast_months": forecast_months,
                "forecast_total": forecast_total,
                "forecast_model": "new_product_forecast" if is_new else "old_product_forecast",
            }
        logger.warning(f"[{product.asin}] forecast_all_months 无有效预测(total={forecast_total})，回退旧模型")
    except Exception as e:
        logger.warning(f"[{product.asin}] forecast_all_months 失败: {e}，回退旧模型")
    return _forecast_sales_legacy(product, history, seasonal_curve)


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


async def _resolve_lead_time(product: Product, session: AsyncSession) -> int:
    """解析大货工期：优先产品实际填写，否则按分类工期表(category_leadtimes)匹配，兜底30天

    匹配规则：按一级分类精确匹配，取该分类下所有记录工期的中间值平均。
    """
    if product.lead_time:
        return int(product.lead_time)

    category = (product.category or "").strip()
    if category:
        rows = (await session.execute(
            select(CategoryLeadtime).where(CategoryLeadtime.level1_category == category)
        )).scalars().all()
        mids = []
        for r in rows:
            lo = r.lead_time_min
            hi = r.lead_time_max
            if lo and hi:
                mids.append((lo + hi) // 2)
            elif lo:
                mids.append(lo)
            elif hi:
                mids.append(hi)
        if mids:
            return round(sum(mids) / len(mids))

    return 30


def _calc_purchase_trigger(forecast: dict, inventory: dict, product: Product, lead_time: int) -> dict:
    """计算采购触发判断（需求文档第九章）

    库存售卖天数 = 可用库存数量 ÷ (最近30天销量 ÷ 30 × 趋势修正系数)
    补货周期 = 大货工期 + 运输时间（海运淡季30/旺季45、空派淡季10/旺季15、快递淡季3/旺季6）
    库存售卖天数 < 补货周期(海运) → 建议采购；越短周期越紧急（空派/快递）
    """
    available_stock = inventory.get("available_stock", 0)
    thirty_volume = inventory.get("thirty_volume") or forecast.get("thirty_volume") or 0

    # 日均销量: 最近30天销量 ÷ 30 × 趋势修正系数
    # 趋势修正系数取自销售趋势（growth_rate 折算）
    trend_coeff = forecast.get("trend_coeff", 1.0) or 1.0
    base_daily = thirty_volume / 30 if thirty_volume > 0 else 0
    daily_sales = base_daily * trend_coeff

    if daily_sales > 0:
        inventory_days = round(available_stock / daily_sales)
    else:
        # 无销量数据时回退到预测估算
        forecast_total = forecast.get("forecast_total", 0)
        daily_sales = forecast_total / 180 if forecast_total > 0 else 0
        inventory_days = round(available_stock / daily_sales) if daily_sales > 0 else 999

    # 更新库存覆盖天数
    inventory["inventory_days"] = inventory_days

    # 运输时间（含亚马逊上架时间）：物流淡季1-7月 / 旺季8-12月
    is_peak = 8 <= date.today().month <= 12
    sea_days = settings.SEA_PEAK_DAYS if is_peak else settings.SEA_SLOW_DAYS
    air_days = settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS
    express_days = settings.EXPRESS_PEAK_DAYS if is_peak else settings.EXPRESS_SLOW_DAYS
    sea_cycle = lead_time + sea_days
    air_cycle = lead_time + air_days
    express_cycle = lead_time + express_days
    transport_cycles = {"sea": sea_cycle, "air": air_cycle, "express": express_cycle}
    inventory["replenishment_cycle"] = sea_cycle

    # 触发条件：库存覆盖天数 < 海运补货周期（最经济方式）
    if inventory_days < sea_cycle:
        if inventory_days < express_cycle:
            recommended = "快递"
        elif inventory_days < air_cycle:
            recommended = "空派"
        else:
            recommended = "海运"
        return {
            "purchase_trigger": "需要采购",
            "reason": f"库存仅覆盖{inventory_days}天（日均{base_daily:.1f}×趋势{trend_coeff:.2f}），低于补货周期{sea_cycle}天(工期{lead_time}+海运{sea_days})，建议{recommended}（空派{air_cycle}天/快递{express_cycle}天）",
            "inventory_days": inventory_days,
            "replenishment_cycle": sea_cycle,
            "transport_cycles": transport_cycles,
            "recommended_transport": recommended,
        }
    else:
        return {
            "purchase_trigger": "无需采购",
            "reason": f"库存可覆盖{inventory_days}天（日均{base_daily:.1f}×趋势{trend_coeff:.2f}），高于补货周期{sea_cycle}天(工期{lead_time}+海运{sea_days})",
            "inventory_days": inventory_days,
            "replenishment_cycle": sea_cycle,
            "transport_cycles": transport_cycles,
            "recommended_transport": "无需采购",
        }


def _calc_suggested_qty(forecast: dict, inventory: dict, product: Product) -> int:
    """计算建议采购数量（最低采购量=未来3个月需求-当前库存，向上取整至箱规倍数）"""
    months = forecast.get("forecast_months") or []
    three_month_demand = sum(m.get("forecast_qty", 0) for m in months[:3])
    available_stock = inventory.get("available_stock", 0)
    box_qty = _safe_int(product.box_quantity, 1)

    # 建议数量 = 未来3个月预测需求 - 当前可用库存
    suggested = three_month_demand - available_stock

    if suggested <= 0:
        return 0

    # 向上取整至装箱数量倍数（如 10000/300=33.33 → 34箱）
    suggested = ceil(suggested / box_qty) * box_qty

    return suggested


async def _calc_suggested_qty_v2(forecast: dict, inventory: dict, product: Product,
                                 lead_time: int, life_cycle: str, session: AsyncSession) -> tuple:
    """计算建议采购数量（主流程整合 v3：接入 new_product_policy 节日/长期补货策略）

    主建议量 = 未来3个月需求 - 当前库存（向上取整箱规倍数，用户指定规则）
    节日产品: 额外用 festival_replenishment_plan 计算销售窗口补货量，取较大值
    返回 (建议数量, 策略说明)
    """
    base_qty = _calc_suggested_qty(forecast, inventory, product)
    note = ""

    # 节日产品：用节日窗口补货策略增强
    if product.festival and base_qty > 0:
        try:
            festival_info = await get_festival_info(product.festival, session)
            festival_date = festival_info.get("festival_date") if festival_info else None
            if festival_date:
                history = await _analyze_sales_history(product.asin, session)
                base_daily = history.get("recent_3_months_avg", 0) / 30
                if base_daily <= 0:
                    base_daily = forecast.get("forecast_total", 0) / 180
                is_decoration = (product.sub_category or "").strip() == "装饰品"
                plan = new_product_policy.festival_replenishment_plan(
                    base_daily_sales=base_daily,
                    lifecycle=life_cycle or "成熟期",
                    available_stock=inventory.get("available_stock", 0),
                    lead_time=lead_time,
                    festival_date=festival_date,
                    is_decoration=is_decoration,
                )
                rec = plan.get("recommended")
                if rec and rec.get("qty", 0) > 0:
                    box_qty = _safe_int(product.box_quantity, 1)
                    window_qty = new_product_policy.round_to_box(rec["qty"], box_qty)
                    if window_qty > base_qty:
                        base_qty = window_qty
                        note = f"节日窗口策略建议{window_qty}（窗口{rec.get('selling_days', 0)}天）> 基础建议，采用"
                    else:
                        note = f"节日窗口策略建议{window_qty} ≤ 基础建议，采用基础建议"
        except Exception as e:
            logger.warning(f"[{product.asin}] 节日补货策略计算失败: {e}")

    return base_qty, note


def _plan_batches(suggested_qty: int, product: Product, lead_time: int) -> dict:
    """规划采购批次"""
    if suggested_qty <= 0:
        return {"batches": [], "total_qty": 0}

    box_qty = _safe_int(product.box_quantity, 1)
    is_peak = 8 <= date.today().month <= 12
    air_days = settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS
    sea_days = settings.SEA_PEAK_DAYS if is_peak else settings.SEA_SLOW_DAYS

    # 简单分批：第一批走空运（急），第二批走海运（不急）
    if suggested_qty <= box_qty * 2:
        batches = [
            {"batch_no": 1, "qty": suggested_qty, "method": "空运", "days": lead_time + air_days},
        ]
    else:
        first_batch = ceil(suggested_qty * 0.3 / box_qty) * box_qty
        second_batch = suggested_qty - first_batch
        batches = [
            {"batch_no": 1, "qty": first_batch, "method": "空运", "days": lead_time + air_days},
            {"batch_no": 2, "qty": second_batch, "method": "海运", "days": lead_time + sea_days},
        ]

    return {
        "batches": batches,
        "total_qty": suggested_qty,
    }


def _calc_score(forecast: dict, inventory: dict, life_cycle: str,
                trigger: dict, product: Product, purchase_window: dict = None) -> dict:
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
        "启动期": 70, "增长期": 90, "热卖期": 100,
        "成熟期": 60, "下降期": 20,
        "新品期": 90, "成长期": 80, "衰退期": 20, "未知": 50,
    }
    life_score = life_cycle_scores.get(life_cycle, 50)

    # 库存紧急（10%）
    urgency = max(0, min(100, round((1 - inventory_days / max(replenishment_cycle, 1)) * 100)))

    # 运输可达（10%）- 使用时间轴判断
    if purchase_window:
        if purchase_window.get("can_purchase", True):
            transport = purchase_window.get("recommended_transport", "海运")
            if transport == "海运":
                transport_score = 100
            elif transport == "空派":
                transport_score = 80
            elif transport == "快递":
                transport_score = 60
            else:
                transport_score = 50
        else:
            transport_score = 0
    else:
        transport_score = 70  # 默认

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
