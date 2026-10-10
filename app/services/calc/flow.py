"""计算主流程（C1 拆分批4 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
含：StepRecorder、单 ASIN 全流程、到期/按等级批量、下一个计算时点、日报汇总与指标辅助函数。
对外入口不变：app.tasks.calculation_tasks.<name> 仍可用（薄壳显式导入本模块）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.ai_evaluation import AiEvaluation
from app.models.calculation import (CalculationResult, CalculationSkipLog, CalculationStepResult)
from app.models.operator import Operator
from app.models.product import Product
from app.models.sales import SalesData
from app.services import ai_eval, new_product_policy
from app.services.sales_fallback import get_daily_sales_dual, sum_daily_sales_dual
from app.services.semantic_classify import buffer_days, get_semantic_classification
from app.services.time_axis import (check_purchase_window, get_festival_info, get_sales_phase, resolve_lead_time)
from app.services.calc import schedule as _schedule
from app.services.calc.common import (
    _fetch_acos,
    _get_new_product_cfg,
    _parse_step_json,
    _recent_daily_orders,
    _safe_float,
    _safe_int,
    _to_float_safe,
)
from app.services.calc.forecast import (
    TREND_COEFF_MAX,
    _analyze_sales_history,
    _fmt_trend_coeff,
    _forecast_sales,
    _is_new_product,
)
from app.services.calc.inventory import (
    _analyze_inventory,
    _arbitrate_verdicts,
    _calc_festival_window,
    _check_hard_rules,
    _check_long_term_stock_cover,
    _check_second_peak_replenishment,
    _identify_lifecycle,
    _normalize_verdict,
    _old_product_inventory_days,
    _run_new_product_flow,
    _window_coverage,
)
from app.services.calc.schedule import (
    _ALL_LEVELS,
    _load_effective_last_dates,
    _load_frequency_config,
    _load_report_lifecycles,
    _normalize_levels,
    _normalize_lifecycles,
    get_calculation_frequency_days,
    get_product_level,
    is_calculation_due,
    normalize_level,
)
from app.services.calc.score import (
    _build_batch_ai_context,
    _calc_score,
    _format_batch_plan,
    _format_score_detail,
    _plan_batches,
    _six_dim_total,
)
from app.services.calc.suggest import (_calc_purchase_trigger, _calc_suggested_qty_v2,
                                       _festival_window_air_catchable)

logger = logging.getLogger(__name__)


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
        logger.info("Step %s %s %s", step_no, step_name, status)

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


def _stockout_risk_reason(result, danger_days: int | None = None) -> str | None:
    """判断单条计算结果是否存在断货风险，并返回人类可读的原因（无风险返回 None）

    判定口径（满足任一即视为断货风险）：
      1. 可用库存 <= 0；
      2. 库存可售天数 < 补货周期（补货来不及到货）；
      3. 库存可售天数 < 危险阈值（inventory_danger_max_days，默认15天）。
    """
    days = result.inventory_days
    cycle = result.replenishment_cycle
    stock = result.available_stock
    if stock is not None and stock <= 0:
        return "可用库存为0，已无货可售"
    if days is not None and cycle is not None and days < cycle:
        return f"库存仅覆盖{days}天，低于补货周期{cycle}天"
    if days is not None and danger_days is not None and days < danger_days:
        return f"库存仅覆盖{days}天，低于危险阈值{danger_days}天"
    return None


def _select_daily_alerts(immediate: list, observe: list, stockout: list,
                         limit: int = 10, risk_reserve: int = 3) -> list:
    """日报重点提醒选择：立即采购（评分降序）→ 观察 → 断货风险（可选兜底）

    risk_reserve=0 时仅返回 立即采购+观察（按评分决策），不包含断货风险兜底；
    断货风险产品即使未进入“立即采购/观察”，也至少保留 risk_reserve 个展示位，
    避免出现“今日无提醒”但实际存在断货风险的情况。
    """
    immediate_asins = {r.asin for r in immediate}
    observe_asins = {r.asin for r in observe}
    immediate_sorted = sorted(immediate, key=lambda r: _safe_float(r.purchase_score), reverse=True)
    observe_sorted = sorted(observe, key=lambda r: _safe_float(r.purchase_score), reverse=True)
    rows = (immediate_sorted + observe_sorted)[:limit]
    if risk_reserve > 0:
        risk_only = sorted(
            (r for r in stockout if r.asin not in immediate_asins and r.asin not in observe_asins),
            key=lambda r: r.inventory_days if r.inventory_days is not None else 99999,
        )
        reserve = min(risk_reserve, len(risk_only))
        rows = rows[: limit - reserve]
        rows.extend(risk_only[: limit - len(rows)])
    return rows


def _to_json(obj) -> str | None:
    """对象转JSON字符串，失败返回None"""
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        return None


def _report_score(r) -> float | None:
    """日报展示评分：有 purchase_score 直接用；新品六维总分仅在已作出决策
    （非 未触发/终止）时回填，避免“未触发却显示高分”的矛盾"""
    if r.purchase_score is not None:
        return r.purchase_score
    if (r.purchase_level or "") in ("未触发", "终止"):
        return None
    return _six_dim_total(_parse_step_json(r.score_detail))


def _analyze_sales_trend(daily_qty: list) -> dict:
    """近14天日销量 → 销量趋势分析（近7天 vs 前7天环比）

    判定口径（与 AI 评估一致）：
      近7天 > 前7天×1.1 → 上升；近7天 < 前7天×0.9 → 下降；否则平稳。
    数据不足7天时 prev7 记 0，有销量即视为上升，无销量记“无销量”。
    """
    qs = [int(q or 0) for q in daily_qty]
    last7 = sum(qs[-7:])
    prev7 = sum(qs[-14:-7]) if len(qs) > 7 else 0
    if prev7 > 0:
        change = round((last7 - prev7) / prev7 * 100)
        if last7 > prev7 * 1.1:
            direction = "上升"
        elif last7 < prev7 * 0.9:
            direction = "下降"
        else:
            direction = "平稳"
    elif last7 > 0:
        change = None
        direction = "上升"
    else:
        change = None
        direction = "无销量"
    text = f"近7天{last7}件 vs 前7天{prev7}件"
    if change is not None:
        text += f"（{change:+d}%）"
    text += f"，趋势{direction}"
    return {
        "last7": last7,
        "prev7": prev7,
        "change_percent": change,
        "direction": direction,
        "text": text,
    }


def _product_ad_profit_metrics(product) -> dict:
    """联合广告与利润数据（来自产品档案/领星，回填后自动带出）

    - 广告：7/30天花费（领星实时同步，已覆盖）、30天广告花费占销售额比（近似ACOS）
    - 利润：profit_rate（领星利润报表回填）、acos_30d（手动回填）、
      三渠道成本表 cost_table（海运/空派/快递单件利润+毛利率，口径与新品策略一致）
    """
    price = _to_float_safe(getattr(product, "price", None))
    # 采购单价统一按 unit_cost 口径（cg_price 人民币单件价 ÷ 汇率 → 美元）
    cost_price = new_product_policy.unit_cost(product)
    cost_price_cny = getattr(product, "cost_price", None)
    seven_spend = _to_float_safe(getattr(product, "seven_spend", None))
    thirty_spend = _to_float_safe(getattr(product, "thirty_spend", None))
    thirty_amount = _to_float_safe(getattr(product, "thirty_amount", None))
    acos_30d = getattr(product, "acos_30d", None)
    profit_rate = getattr(product, "profit_rate", None)

    ad_spend_ratio = None
    if thirty_amount > 0:
        ad_spend_ratio = round(thirty_spend / thirty_amount * 100, 2)

    est_profit_rate = None
    cost_table = None
    try:
        cost_table = new_product_policy.calc_cost_table(product)
        sea_margin = (cost_table.get("channels") or {}).get("sea", {}).get("margin")
        if sea_margin is not None:
            est_profit_rate = sea_margin
    except Exception:
        est_profit_rate = None

    return {
        "price": round(price, 2) if price else None,
        "cost_price": round(cost_price, 4) if cost_price else None,
        "cost_price_cny": _to_float_safe(cost_price_cny) if cost_price_cny not in (None, "") else None,
        "seven_spend": round(seven_spend, 2) if seven_spend else None,
        "thirty_spend": round(thirty_spend, 2) if thirty_spend else None,
        "thirty_amount": round(thirty_amount, 2) if thirty_amount else None,
        "ad_spend_ratio": ad_spend_ratio,
        "acos_30d": acos_30d,
        "profit_rate": profit_rate,
        "est_profit_rate": est_profit_rate,
        "cost_table": cost_table,
    }


async def _latest_calculation_rows(session: AsyncSession) -> list:
    """每个ASIN最近一次计算结果（今日未跑批量时用作看板兜底数据源）"""
    latest_sub = (
        select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
        .group_by(CalculationResult.asin)
        .subquery()
    )
    rows = await session.execute(
        select(CalculationResult).join(
            latest_sub,
            and_(CalculationResult.asin == latest_sub.c.asin, CalculationResult.calc_date == latest_sub.c.d),
        ).join(Product, Product.asin == CalculationResult.asin).where(Product.status == True)  # noqa: E712
    )
    return rows.scalars().all()


async def get_daily_summary(session: AsyncSession, include_results: bool = True,
                            levels: str | list[str] | None = None,
                            lifecycles: str | list[str] | set[str] | None = None) -> dict:
    """生成日报汇总数据（include_results=False 时不返回全量明细，用于看板轻量加载）

    levels: 可选产品等级过滤（如 "SAB" 或 ["S","A","B"]），None=不限制（默认全部等级）
    lifecycles: 可选生命周期过滤（如 "启动期,增长期,热卖期"），None/空=不限制
    """
    today = date.today()

    # 只统计最新计算逻辑（logic_version=1）的结果：
    # B/C/D 未按新逻辑刷新前，日报先以 S/A 新结果校准（避免旧结果污染数字）
    fresh_rows = (await session.execute(
        select(CalculationResult).where(
            CalculationResult.calc_date == today,
            CalculationResult.logic_version == 1,
        )
    )).scalars().all()
    if fresh_rows:
        all_results = fresh_rows
        data_date = today
        data_scope = "fresh_logic_v1"
    else:
        all_results = await _latest_calculation_rows(session)
        data_date = max((r.calc_date for r in all_results), default=today)
        data_scope = "latest_fallback"

    # 仅统计在售产品（排除停售/已删除），可选按产品等级过滤
    if levels is None:
        level_filter: set[str] | None = None
    else:
        lv_str = ",".join(levels) if isinstance(levels, (list, tuple, set)) else levels
        level_filter = set(_normalize_levels(lv_str))
    all_asins = [r.asin for r in all_results]
    life_filter = _normalize_lifecycles(lifecycles) or None
    active_map: dict[str, str] = {}
    life_map: dict[str, str] = {}
    if all_asins:
        active_rows = await session.execute(
            select(Product.asin, Product.product_level, Product.life_cycle).where(
                Product.asin.in_(all_asins), Product.status == True  # noqa: E712
            )
        )
        for row in active_rows.all():
            active_map[row[0]] = row[1]
            life_map[row[0]] = row[2]
    all_results = [
        r for r in all_results
        if r.asin in active_map
        and (level_filter is None or (active_map[r.asin] or "").upper() in level_filter)
        and (life_filter is None or (life_map.get(r.asin) or "未知").strip() in life_filter)
    ]

    total_asins = len(all_results)

    # 销量趋势分析：近14天日销量 → 近7天 vs 前7天环比（供结果明细展示）
    sales_trend_map: dict[str, dict] = {}
    if all_results:
        sales_rows = await session.execute(
            select(SalesData.asin, SalesData.date, SalesData.sales_qty)
            .where(SalesData.asin.in_([r.asin for r in all_results]),
                   SalesData.date >= today - timedelta(days=13))
            .order_by(SalesData.date)
        )
        trend_qty: dict[str, list] = {}
        seen: set[str] = set()
        for a, _d, q in sales_rows.all():
            trend_qty.setdefault(a, []).append(int(q or 0))
            seen.add(a)
        # 双源优先：SalesData 近14天无记录的 ASIN，从 daily_sales_stats 取近14天日销量
        fb_start = today - timedelta(days=13)
        for r in all_results:
            a = r.asin
            if a in seen:
                continue
            fb = await get_daily_sales_dual(a, fb_start, today, session, use_zero=False)
            if fb:
                trend_qty[a] = [q for _d, q in sorted(fb.items())]
            else:
                trend_qty[a] = []
        sales_trend_map = {a: _analyze_sales_trend(qs) for a, qs in trend_qty.items()}

    # 成本表（三渠道）盈利汇总：联合售价/成本/运费/FBA/佣金判断各渠道是否可盈利
    from types import SimpleNamespace

    cost_summary = {"sea": 0, "air": 0, "express": 0, "all_loss": 0, "total": 0}
    if all_results:
        cost_prod_rows = await session.execute(
            select(Product.asin, Product.price, Product.cost_price, Product.box_quantity,
                   Product.fba_fee, Product.referral_fee)
            .where(Product.asin.in_([r.asin for r in all_results]))
        )
        for row in cost_prod_rows.all():
            cost_prod = SimpleNamespace(
                price=row[1], cost_price=row[2], box_quantity=row[3],
                fba_fee=row[4], referral_fee=row[5],
            )
            try:
                ct = new_product_policy.calc_cost_table(cost_prod)
            except Exception:
                continue
            cost_summary["total"] += 1
            for mode in ("sea", "air", "express"):
                if (ct.get("channels") or {}).get(mode, {}).get("profitable"):
                    cost_summary[mode] += 1
            if not ct.get("all_profitable"):
                cost_summary["all_loss"] += 1

    # 按采购级别统计
    # 级别三档化：建议采购=立即采购；未触发/终止=暂停
    immediate = [r for r in all_results if normalize_level(r.purchase_level) == "立即采购"]
    observe = [r for r in all_results if normalize_level(r.purchase_level) == "观察"]
    pause = [r for r in all_results if normalize_level(r.purchase_level) == "暂停"]

    # 断货风险识别：库存可售天数 < 补货周期 / < 危险阈值，或可用库存为0
    from app.services.config_service import get_param

    danger_days = await get_param(session, "inventory_danger_max_days") or settings.INVENTORY_DANGER_MAX_DAYS
    # 新品豁免：新品走门禁流程（触发条件/ACOS/成本表），未触发或终止时
    # 可用库存=0 属上架初期正常现象（货在途中/入库中），不进断货风险提醒；
    # 仅当新品已触发采购（建议采购/需要采购）才参与断货风险判定。
    new_asins: set[str] = set()
    if all_results:
        new_rows = await session.execute(
            select(Product.asin).where(
                Product.asin.in_([r.asin for r in all_results]),
                Product.product_stage == "新品",
            )
        )
        new_asins = {row[0] for row in new_rows.all()}
    stockout = [
        r for r in all_results
        if _stockout_risk_reason(r, danger_days)
        and not (
            r.asin in new_asins
            and (r.purchase_trigger or "") in ("未触发", "终止加订")
        )
    ]
    stockout_count = len(stockout)
    pause_stockout_count = len([r for r in stockout if normalize_level(r.purchase_level) == "暂停"])
    # 库存积压数量（> 健康警戒线天数，默认90天），供大屏 KPI 使用
    healthy_days = int(await get_param(session, "inventory_healthy_max_days") or settings.INVENTORY_HEALTHY_MAX_DAYS)
    overstock_count = len([
        r for r in all_results
        if r.inventory_days is not None and r.inventory_days > healthy_days
    ])

    # 重点提醒列表（立即采购/观察按评分决策，并保留断货风险兜底）：
    #  1) 立即采购（评分降序）优先；
    #  2) 观察产品次之；
    #  3) 断货风险产品兜底（预留 risk_reserve 个展示位），
    #     避免出现“今日无提醒”但实际存在断货风险的情况。
    alert_rows = _select_daily_alerts(immediate, observe, stockout, limit=10, risk_reserve=3)
    alert_asins = [r.asin for r in alert_rows]
    immediate_asins = {r.asin for r in immediate}
    observe_asins = {r.asin for r in observe}

    # 批量取产品信息（品名/负责人）
    prod_rows = await session.execute(
        select(Product.asin, Product.product_name, Product.operator, Product.primary_operator).where(Product.asin.in_(alert_asins))
    ) if alert_asins else None
    prod_map = {row[0]: row for row in (prod_rows.all() if prod_rows else [])}

    # 负责人白名单：清洗后只保留白名单内人员（白名单为空则不限制）
    from app.services.operator_sync import clean_operator_name

    whitelist_raw = await get_param(session, "operator_whitelist") or ""
    whitelist = {n.strip() for n in str(whitelist_raw).split(",") if n.strip()}

    # 负责人飞书UID（按清洗后的姓名匹配运营人员表）
    all_op_names: set[str] = set()
    for row in prod_map.values():
        for name in str(row[2] or "").split(","):
            clean = clean_operator_name(name)
            if clean:
                all_op_names.add(clean)
    op_map: dict[str, str] = {}
    if all_op_names:
        op_rows = await session.execute(
            select(Operator.name, Operator.feishu_user_id).where(Operator.name.in_(all_op_names))
        )
        op_map = {r[0]: r[1] for r in op_rows.all() if r[1]}

    # 最近一次 AI 采购评估（原因展示用）
    ai_map: dict[str, str] = {}
    if alert_asins:
        ai_rows = await session.execute(
            select(AiEvaluation.asin, AiEvaluation.output_data)
            .where(AiEvaluation.asin.in_(alert_asins),
                   AiEvaluation.eval_type.in_(("purchase_advice", "daily_alert")))
            .order_by(AiEvaluation.created_at.desc())
        )
        for row in ai_rows.all():
            if row[0] not in ai_map:
                try:
                    out = json.loads(row[1] or "{}")
                except (json.JSONDecodeError, TypeError):
                    out = {}
                ai_map[row[0]] = out.get("reason") or out.get("conclusion") or ""

    top_alerts = []
    for r in alert_rows:
        prod = prod_map.get(r.asin)
        operators = []
        for n in str(prod[2] if prod else "").split(","):
            name = clean_operator_name(n)
            if name and (not whitelist or name in whitelist) and name not in operators:
                operators.append(name)
        risk_reason = _stockout_risk_reason(r, danger_days)
        if r.asin in immediate_asins:
            alert_type, reason = "立即采购", risk_reason or r.purchase_trigger or ""
        elif r.asin in observe_asins:
            alert_type, reason = "观察", risk_reason or r.purchase_trigger or ""
        else:
            alert_type, reason = "断货风险", risk_reason or ""
        top_alerts.append({
            "asin": r.asin,
            "alert_type": alert_type,
            "alert_reason": reason,
            "product_name": prod[1] if prod else "",
            "operator": prod[2] if prod else None,
            "primary_operator": prod[3] if prod else None,
            "operators": operators,
            "feishu_user_ids": [op_map[n] for n in operators if op_map.get(n)],
            "purchase_level": r.purchase_level,
            "purchase_score": _report_score(r),
            "base_score": r.base_score,
            "suggested_qty": r.suggested_qty,
            "inventory_days": r.inventory_days,
            "available_stock": r.available_stock,
            "purchase_trigger": r.purchase_trigger,
            "ai_analysis": ai_map.get(r.asin) or None,
        })

    # 今日若已有 AI 日报总结，直接带出
    ai_daily = (await session.execute(
        select(AiEvaluation.output_data)
        .where(AiEvaluation.asin == "__daily__", AiEvaluation.eval_type == "daily_report",
               AiEvaluation.status == "success", AiEvaluation.calc_date == today)
        .order_by(AiEvaluation.created_at.desc()).limit(1)
    )).scalar_one_or_none()

    summary = {
        "calc_date": today.isoformat(),
        "data_date": data_date.isoformat(),
        "data_scope": data_scope,
        "total_asins": total_asins,
        "immediate_count": len(immediate),
        "observe_count": len(observe),
        "pause_count": len(pause),
        "stockout_count": stockout_count,
        "pause_stockout_count": pause_stockout_count,
        "overstock_count": overstock_count,
        "cost_summary": cost_summary,
        "top_alerts": top_alerts,
    }
    if ai_daily:
        try:
            summary["ai_summary"] = json.loads(ai_daily).get("summary", "")
        except (json.JSONDecodeError, TypeError):
            pass
    if include_results:
        # 结果明细：附带品名/负责人/等级 + 完整分析字段（库存、周期、预测、触发、评分明细、批次、广告、利润、AI分析）
        result_asins = [r.asin for r in all_results]
        res_prod_map: dict[str, Product] = {}
        if result_asins:
            res_rows = await session.execute(
                select(Product)
                .where(Product.asin.in_(result_asins))
            )
            res_prod_map = {row[0].asin: row[0] for row in res_rows.all()}

        # 最近一次 AI 采购评估（分析展示用）
        res_ai_map: dict[str, str] = {}
        if result_asins:
            ai_rows = await session.execute(
                select(AiEvaluation.asin, AiEvaluation.output_data)
                .where(AiEvaluation.asin.in_(result_asins),
                       AiEvaluation.eval_type.in_(("purchase_advice", "daily_alert")))
                .order_by(AiEvaluation.created_at.desc())
            )
            for row in ai_rows.all():
                if row[0] not in res_ai_map:
                    try:
                        out = json.loads(row[1] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        out = {}
                    res_ai_map[row[0]] = out.get("reason") or out.get("conclusion") or ""

        # 排序：立即采购 → 观察 → 暂停 → 其他，组内评分降序（无评分排后）
        level_order = {"立即采购": 0, "观察": 1, "暂停": 2}
        all_results = sorted(
            all_results,
            key=lambda r: (
                level_order.get(normalize_level(r.purchase_level) or "", 3),
                r.purchase_score is None,
                -(_safe_float(r.purchase_score) if r.purchase_score is not None else 0),
            ),
        )

        summary["results"] = []
        for r in all_results:
            prod = res_prod_map.get(r.asin)
            ad_metrics = _product_ad_profit_metrics(prod) if prod else {}
            summary["results"].append({
                "asin": r.asin,
                "product_name": prod.product_name if prod else "",
                "operator": prod.operator if prod else None,
                "primary_operator": prod.primary_operator if prod else None,
                "product_level": prod.product_level if prod else None,
                "purchase_level": normalize_level(r.purchase_level),
                "purchase_score": _report_score(r),
                "base_score": r.base_score,
                "suggested_qty": r.suggested_qty,
                "inventory_days": r.inventory_days,
                "available_stock": r.available_stock,
                "replenishment_cycle": r.replenishment_cycle,
                "forecast_total": r.forecast_total,
                "forecast_months": _parse_step_json(r.forecast_months),
                "purchase_trigger": r.purchase_trigger,
                "urgency_score": r.urgency_score,
                "batch_plan": _parse_step_json(r.batch_plan),
                "batch_plan_text": _format_batch_plan(_parse_step_json(r.batch_plan)),
                "score_detail": _parse_step_json(r.score_detail),
                "score_detail_text": _format_score_detail(_parse_step_json(r.score_detail)),
                "sales_trend": sales_trend_map.get(r.asin) or None,
                "ai_analysis": res_ai_map.get(r.asin) or None,
                **ad_metrics,
            })
    return summary


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

    # ── 分析前基础数据校验：新老品/产品等级/生命周期缺失 → 跳过分析，不计算不出分 ──
    _missing = []
    if (product.product_stage or "") not in ("新品", "老品"):
        _missing.append("新老品")
    if (product.product_level or "").upper() not in ("S", "A", "B", "C", "D"):
        _missing.append("产品等级")
    if not (product.life_cycle or "").strip():
        _missing.append("生命周期")
    if _missing:
        reason = f"基础数据缺失（{'/'.join(_missing)}），跳过分析，请先执行基础数据刷新"
        logger.warning("跳过计算 ASIN=%s: %s", asin, reason)
        return {"asin": asin, "skipped": True, "reason": reason, "calc_date": calc_date.isoformat()}

    # ── 分析前节日门禁：节日产品的「节日时间」须落在未来 6 个月（今天+180天）内 ──
    # 仅节日产品（festival 非空且非长期产品）参与；节日已过者按「下一次节日到来」判断。
    # 不在窗口内 → 静默跳过：不写 calculation_results（页面无记录、日报不计入），
    # 仅在 calculation_skip_logs 留一条「静默跳过」记录便于排查。
    try:
        from app.services.festival_lifecycle import next_festival_date

        festival_name, next_date = await next_festival_date(product, session, calc_date)
    except Exception:  # noqa: BLE001
        festival_name, next_date = None, None
    window_end = calc_date + timedelta(days=180)
    if next_date is not None and next_date > window_end:
        reason = (
            f"节日门禁：下一次节日「{festival_name or product.festival}」{next_date} "
            f"距今 {(next_date - calc_date).days} 天，超过未来 180 天（截止 {window_end}），静默跳过"
        )
        logger.info("静默跳过计算 ASIN=%s: %s", asin, reason)
        # 当日重复触发时覆盖旧记录，避免留痕重复
        await session.execute(
            delete(CalculationSkipLog).where(
                CalculationSkipLog.asin == asin,
                CalculationSkipLog.calc_date == calc_date,
                CalculationSkipLog.gate == "节日门禁",
            )
        )
        session.add(CalculationSkipLog(
            asin=asin,
            calc_date=calc_date,
            gate="节日门禁",
            festival=(product.festival or None),
            festival_name=festival_name,
            next_festival_date=next_date,
            days_until=(next_date - calc_date).days,
            reason=reason,
        ))
        await session.flush()
        return {"asin": asin, "skipped": True, "reason": reason, "calc_date": calc_date.isoformat()}

    # 成本表覆盖字段（product_costs，可维护/导入），calc_cost_table 自动读取
    from app.models.product_cost import ProductCost

    cost_row = (await session.execute(
        select(ProductCost).where(ProductCost.asin == asin)
    )).scalar_one_or_none()
    if cost_row is not None:
        product._cost_override = {
            k: getattr(cost_row, k)
            for k in (
                "price", "cost_cny", "exchange_rate", "length_cm", "width_cm", "height_cm",
                "weight_kg", "freight_sea_cny", "freight_air_cny", "freight_express_cny",
                "sorting_fee", "referral_fee", "packing_fee", "inbound_fee", "storage_fee",
                "ad_fee", "return_loss", "over_threshold_loss", "misc_fee", "notes",
            )
            if getattr(cost_row, k) is not None
        }
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
    life_cycle = await _identify_lifecycle(product, session)
    if product.life_cycle != life_cycle:
        product.life_cycle = life_cycle  # 计算即落库，避免“算过但没落库”
    # 生命周期阶段结束日期（节日时间点表；无结束日期 → None）
    lifecycle_end_info = None
    try:
        from app.services.festival_lifecycle import lifecycle_stage_end

        lifecycle_end_info = await lifecycle_stage_end(product, session, calc_date)
    except Exception:  # noqa: BLE001
        lifecycle_end_info = None
    lifecycle_end = lifecycle_end_info[1] if lifecycle_end_info else None
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

    # ── Step 3: 销售时间轴判断 ──
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

    # ── Step 4: 分析历史销量（按品分化：新品看上架天数/近期单量，老品看同比环比） ──
    is_new_product = await _is_new_product(product, session)
    history = await _analyze_sales_history(asin, session, product=product, is_new=is_new_product)
    if is_new_product:
        recorder.record(4, "历史销量分析（新品）", {
            "list_date": history.get("list_date"),
            "days_on_sale": history.get("days_on_sale"),
            "recent_3d_qty": history.get("recent_3d_qty"),
            "recent_7d_qty": history.get("recent_7d_qty"),
            "daily_avg_qty": history.get("daily_avg_qty"),
            "cumulative_sales": history.get("cumulative_sales"),
        }, input_data={
            "asin": asin,
            "list_date": history.get("list_date"),
            "product_stage": product.product_stage,
        }, reason=f"上架天数={history.get('days_on_sale')}天, 最近3天单量={history.get('recent_3d_qty')}, "
                  f"最近7天单量={history.get('recent_7d_qty')}, 日均={history.get('daily_avg_qty')}, "
                  f"累计销量={history.get('cumulative_sales')}（新品不做同比/环比）")
    else:
        # 老品溯源：只保留「去年同窗口」口径（旧口径月均/去年同月/同比/环比/总额一律不展示）
        _step4 = {}
        # 节日老品：去年同窗口各月销量与占比（逐年逐日求和口径，首尾月为不完整月）
        if history.get("monthly_sales"):
            _step4.update({
                "festival": history.get("festival"),
                "window_start": history.get("window_start"),
                "window_end": history.get("window_end"),
                "monthly_sales": history.get("monthly_sales"),
                "base_total": history.get("base_total"),
                "recent_30d_qty": history.get("recent_30d_qty"),
                "last_year_30d_qty": history.get("last_year_30d_qty"),
            })
        # 老品-长期产品：去年同窗口各月销量、占比、趋势系数（Q8 口径）
        if history.get("lt_monthly_sales"):
            _step4.update({
                "long_term_window_start": history.get("lt_window_start"),
                "long_term_window_end": history.get("lt_window_end"),
                "long_term_monthly_sales": history.get("lt_monthly_sales"),
                "long_term_base_total": history.get("lt_base_total"),
                "long_term_recent_30d_qty": history.get("lt_recent_30d_qty"),
                "long_term_last_year_30d_qty": history.get("lt_last_year_30d_qty"),
                "long_term_trend_coeff": history.get("lt_trend_coeff"),
            })
        _reason = ""
        if history.get("monthly_sales"):
            _reason += (f"节日窗口 {history.get('window_start')}~{history.get('window_end')}："
                        f"去年同窗口合计={history.get('base_total')}台"
                        f"（各月逐日求和，首尾月为不完整月），"
                        f"近30天={history.get('recent_30d_qty')} / 去年同30天={history.get('last_year_30d_qty')}")
        if history.get("lt_monthly_sales"):
            _reason += (" | " if _reason else "") + (
                        f"长期产品窗口 {history.get('lt_window_start')}~{history.get('lt_window_end')}："
                        f"去年同窗口合计={history.get('lt_base_total')}台（各月逐日求和，首尾月为不完整月），"
                        f"近30天={history.get('lt_recent_30d_qty')} / 去年同30天={history.get('lt_last_year_30d_qty')}"
                        f"，趋势系数={_fmt_trend_coeff(history.get('lt_trend_coeff'), history.get('lt_trend_coeff_raw'))}"
                        f"（上限{TREND_COEFF_MAX}，超过直接用上限）")
        recorder.record(4, "历史销量分析（老品）", _step4, input_data={"asin": asin}, reason=_reason)

    # ── Step 5: 预测未来销量（老品 forecast.py 模型；新品走第六章窗口口径） ──
    forecast = await _forecast_sales(product, history, session)
    if is_new_product:
        _win = forecast.get("forecast_window") or {}
        recorder.record(5, "未来销量预测（新品窗口口径）", {
            "forecast_branch": forecast.get("forecast_branch"),
            "forecast_basis": forecast.get("forecast_basis"),
            "forecast_window": _win,
            "launch_start": forecast.get("launch_start"),
            "life_cycle": forecast.get("life_cycle"),
            "forecast_total": forecast.get("forecast_total"),
            "forecast_months": forecast.get("forecast_months"),
            "forecast_model": forecast.get("forecast_model"),
        }, reason=f"未来预计总销量={forecast.get('forecast_total')}（{forecast.get('forecast_branch')}，"
                  f"窗口{_win.get('start')}~{_win.get('end')}共{_win.get('days')}天；"
                  f"{forecast.get('forecast_basis')}）")
    else:
        recorder.record(5, "未来销量预测（老品）", {
            "forecast_total": forecast.get("forecast_total"),
            "forecast_months": forecast.get("forecast_months"),
            "forecast_model": forecast.get("forecast_model"),
            "forecast_coeffs": forecast.get("forecast_coeffs"),
        }, reason=f"未来预计总销量={forecast.get('forecast_total')}（模型: {forecast.get('forecast_model', 'no_forecast')}）")

    # ── Step 6: 分析库存健康 ──
    inventory = await _analyze_inventory(asin, session)
    # 30天销量兜底：日快照没有时用产品档案（Listing 快照）字段
    if not inventory.get("thirty_volume") and getattr(product, "thirty_volume", None):
        inventory["thirty_volume"] = _safe_int(product.thirty_volume)
    if not inventory.get("average_thirty_volume") and getattr(product, "average_thirty_volume", None):
        inventory["average_thirty_volume"] = _safe_float(product.average_thirty_volume)
    # ── 老品库存天数唯一口径：窗口「各月预估 ÷ 该月天数 → 按库存坐落月逐月扣减」 ──
    #    必须早于 Step 6 记录：Step 6/7/结果表展示的库存天数为同一口径结果（_old_product_inventory_days）；
    #    节日窗口优先，其次长期产品窗口；去年同窗口无销量（无各月占比）→ 留空（None）。
    #    新品不参与（走 _run_new_product_flow 的新品口径）。
    festival_window = None
    if not is_new_product:
        festival_window = await _calc_festival_window(product, session, inventory)
        if festival_window:
            inventory["festival_window"] = festival_window
            inventory["last_year_thirty_volume"] = festival_window.get("last_year_30d") or 0
            if (festival_window.get("this_year_30d") or 0) > 0:
                # 评分 G 与窗口预估同口径：近30天销量统一取 SalesData 日明细
                inventory["thirty_volume"] = festival_window["this_year_30d"]

        # 老品-长期产品：把 Step 4 的「各月占比」+ Step 5 的「当月预测销量」透传给 Step 9/15
        # 采购批次规划（Q8：由 AI 为最终需采购的产品分割批次）
        if history.get("lt_monthly_sales"):
            lt_win = {
                "window_start": history.get("lt_window_start"),
                "window_end": history.get("lt_window_end"),
                "base_total": history.get("lt_base_total"),
                "trend_coeff": history.get("lt_trend_coeff"),
                "monthly_sales": history.get("lt_monthly_sales"),
                "monthly_forecast": forecast.get("forecast_months"),
            }
            # 库存天数与节日老品同口径：各月预估 ÷ 该月天数 → 按库存坐落月逐月扣减
            #    history 里的 lt_window_start/lt_window_end 为 isoformat 字符串，
            #    _window_coverage 内部需 date 参与比较/相减，这里先归一化
            _lt_ws, _lt_we = lt_win["window_start"], lt_win["window_end"]
            if _lt_ws and _lt_we:
                _lt_cov = _window_coverage(
                    inventory, lt_win,
                    date.fromisoformat(str(_lt_ws)[:10]),
                    date.fromisoformat(str(_lt_we)[:10]),
                )
                lt_win.update({
                    "coverage_days": _lt_cov["days"],
                    "coverage_until": _lt_cov["until"],
                    "coverage_months": _lt_cov["months"],
                    "coverage_demand_total": _lt_cov["demand_total"],
                })
            inventory["long_term_window"] = lt_win

        # ── 老品库存天数唯一入口（四套逻辑中的三套，按优先级落值） ──
        #    ① 老品（唯一口径）窗口各月占比逐月扣减：有窗口各月占比（节日窗口优先 → 长期产品窗口）
        #    ② 老品-数据过少兜底：lx_used → 沿用领星可售天数/预估日销（不参与窗口口径）
        #    ③ 老品-无窗口：无节日且非长期 → 库存天数留空、不触发采购
        _old_cov = _old_product_inventory_days(inventory, festival_window)
        if _old_cov["source"] == "window":
            # ① 唯一口径
            inventory["inventory_days"] = _old_cov["days"]
            inventory["inventory_days_source"] = "window"
            inventory["inventory_days_formula"] = _old_cov["formula"]
        elif inventory.get("lx_used"):
            # ② 领星兜底：沿用 _analyze_inventory 的领星可售天数/预估日销
            inventory["inventory_days_source"] = "lx"
        else:
            # ③ 无窗口：留空。不采用 _analyze_inventory 的系统公式值，否则与 Step 7/结果表（留空）矛盾
            inventory["inventory_days"] = None
            inventory["inventory_days_source"] = "none"
            inventory["inventory_days_formula"] = _old_cov["formula"]

    # ── 库存天数口径可溯源：分析过程溯源里写明本条用的是哪一套逻辑（四套互斥，全集随记录落库）──
    if is_new_product:
        _b6_kind = "新品：库存售卖天数 = 可用库存 ÷ 预测日销"
        _b6_days_formula = ("新品口径：可用库存 ÷ 预测日销（预测日销 = 基准 × 生命周期日销系数 × 安全系数，"
                            "在 Step N1+ 新品决策流程计算并回填）")
        # 新品在 Step 6 尚未进入新品决策流程，库存天数此时未定（不展示 _analyze_inventory 的日均口径值）
        _b6_days_value = None
    elif inventory.get("inventory_days_source") == "window":
        _b6_kind = "老品（唯一口径）：窗口各月占比逐月扣减"
        _b6_days_formula = inventory.get("inventory_days_formula") or "窗口口径"
        _b6_days_value = inventory.get("inventory_days")
    elif inventory.get("lx_used"):
        _b6_kind = "老品-数据过少兜底：领星可售天数 / 预估日销"
        _b6_days_formula = inventory.get("inventory_days_formula") or "领星口径"
        _b6_days_value = inventory.get("inventory_days")
    else:
        _b6_kind = "老品-无窗口：无节日且非长期 → 库存天数留空、不触发采购"
        _b6_days_formula = inventory.get("inventory_days_formula") or "无窗口各月占比，库存天数不计算（留空）"
        _b6_days_value = inventory.get("inventory_days")
    # 四套全集（供溯源对照，标记本条适用哪套）
    _b6_all_kinds = {
        "老品（唯一口径）：窗口各月占比逐月扣减": "各月预估 = 去年该月逐日和 × 趋势系数 → 该月日均 = 该月预估 ÷ 该月天数 → 自窗口首日逐月扣可用库存",
        "老品-数据过少兜底：领星可售天数 / 预估日销": "数据过少（近3天无销量或覆盖<20天）→ lx_used，直接沿用领星可售天数/预估日销",
        "新品：库存售卖天数 = 可用库存 ÷ 预测日销": "新品决策流程内：库存售卖天数 = 可用库存 ÷ 预测日销",
        "老品-无窗口：无节日且非长期 → 库存天数留空、不触发采购": "无任何窗口各月占比 → 库存天数不计算（留空）",
    }
    _b6_all_kinds_marked = {
        k: (f"【本条适用】{v}" if k == _b6_kind else v) for k, v in _b6_all_kinds.items()
    }
    _b6_arrival_formula = inventory.get("inbound_arrival_formula") or "无在途（FBA在途+采购待到货=0），不计算上架日"
    _b6_reason = "\n".join([
        f"可用库存={inventory.get('available_stock')}, 覆盖天数={_b6_days_value}, "
        f"口径={_b6_kind}",
        f"可用库存公式：{inventory.get('available_stock_formula') or '—'}",
        f"覆盖天数公式：{_b6_days_formula}",
        f"在途预计上架日公式：{_b6_arrival_formula}",
    ])
    recorder.record(6, "库存健康分析", {
        "available_stock": inventory.get("available_stock"),
        "fba_available": inventory.get("fba_available"),
        "inventory_days": _b6_days_value,
        "replenishment_cycle": inventory.get("replenishment_cycle"),
        "口径": _b6_kind,
        "覆盖天数口径": _b6_days_formula,
        "库存天数四套逻辑（互斥，标记本条适用）": _b6_all_kinds_marked,
        "可用库存口径": inventory.get("available_stock_formula"),
        "销量数据天数": inventory.get("data_days"),
        "领星可售天数": inventory.get("fba_available_days"),
        "领星预估日销": inventory.get("estimated_daily_sales"),
        "领星售罄日": inventory.get("stockout_date"),
        "FBA在途": inventory.get("fba_inbound"),
        "采购待到货": inventory.get("purchase_on_order"),
        "在途合计": inventory.get("inbound_total"),
        "在途预计上架日": inventory.get("inbound_arrival_date"),
        "在途预计上架日口径": _b6_arrival_formula,
    }, input_data={"asin": asin}, reason=_b6_reason)

    # 解析大货工期：优先产品实际填写，否则按分类工期表(category_leadtimes)匹配
    lead_time = await resolve_lead_time(product, session)

    # ── 新品分支：只使用新品补货策略（需求文档第六章；第七-十四点新品不参与） ──
    _computed_stage = "新品" if is_new_product else "老品"
    if (product.product_stage or "") != _computed_stage:
        product.product_stage = _computed_stage  # 计算即落库，避免“算过但没落库”
    if is_new_product:
        new_flow = await _run_new_product_flow(
            asin, product, session, recorder, calc_date, inventory, life_cycle, lead_time,
            purchase_window,
        )
        trigger = new_flow["trigger"]
        suggested_qty = new_flow["suggested_qty"]
        batch_plan = new_flow["batch_plan"]
        scoring = new_flow["scoring"]
        # 新品「利润空间」：DeepSeek 综合评分（C1广告/C2Listing/C3毛利/C4成本表/C5剩余天数）
        # 作为新品利润空间的唯一分数；未启用/失败 → None → 传 0，不回退线性公式
        new_profit_score = None
        try:
            _ct_for_ai = new_product_policy.calc_cost_table(product)
        except Exception:  # noqa: BLE001
            _ct_for_ai = None
        try:
            _ai_profit = await ai_eval.evaluate_new_product_profit_space(
                product, session,
                cost_table=_ct_for_ai,
                inventory_days=inventory.get("inventory_days"),
            )
            if _ai_profit:
                new_profit_score = _ai_profit.get("profit_score")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{asin}] 新品利润空间 AI 评分异常（按0计）: {e}")
        # 新品也计算六维评分（统一评分明细展示；等级仍以新品门禁为准）
        try:
            six = _calc_score(forecast, inventory, life_cycle, trigger, product, purchase_window, lifecycle_end, True,
                              new_product_profit_score=new_profit_score)
            scoring.setdefault("score_detail", {})["六维评分"] = six["score_detail"]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{asin}] 新品六维评分计算失败: {e}")
    else:
        # ── 老品：节日窗口 / 长期窗口与库存天数已在 Step 6 之前按唯一口径算好 ──
        #    （见 _old_product_inventory_days：各月占比逐月扣减），此处仅消费，不再重算。

        # ── Step 7: 计算采购触发（结合时间轴） ──
        # 老品节日产品：窗口逻辑覆盖时间轴门禁（T1-9）——当窗口剩余需求>0 且空运仍能赶上热卖月时，
        # 允许用空派保热卖（走 _calc_purchase_trigger 的窗口触发）；只有空运也赶不上时才维持禁止采购。
        gate_override = bool(festival_window) and _festival_window_air_catchable(festival_window, lead_time)
        if not purchase_window.get("can_purchase", True):
            if gate_override and festival_window and (festival_window.get("remaining") or 0) > 0:
                inventory["life_cycle"] = life_cycle
                trigger = _calc_purchase_trigger(forecast, inventory, product, lead_time)
                trigger["reason"] += f" | 窗口逻辑覆盖时间轴门禁：{purchase_window.get('reason', '')}"
            else:
                trigger = {
                    "purchase_trigger": "禁止采购",
                    "reason": purchase_window.get("reason", "无法赶上销售窗口"),
                    "inventory_days": inventory.get("inventory_days", 0),
                    "replenishment_cycle": inventory.get("replenishment_cycle", 30),
                }
        else:
            # 将 Step2 识别出的生命周期传入（_calc_purchase_trigger 按趋势修正系数折算30天日均兜底）
            inventory["life_cycle"] = life_cycle
            trigger = _calc_purchase_trigger(forecast, inventory, product, lead_time)
            # 补充时间轴原因
            trigger["reason"] += f" | {sales_phase_info.get('reason', '')}"
        recorder.record(7, "采购触发判断", {
            "purchase_trigger": trigger.get("purchase_trigger"),
            "inventory_days": trigger.get("inventory_days"),
            "库存天数口径": _b6_kind,
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
        # 下降期不采购：触发也降级为无需采购，避免"需要采购但建议0件"的矛盾
        if life_cycle == "下降期" and suggested_qty == 0 and trigger.get("purchase_trigger") == "需要采购":
            trigger["purchase_trigger"] = "无需采购"
            trigger["reason"] = (suggest_note or "生命周期下降期，不采购") + " | " + trigger.get("reason", "")
        # 触发非"需要采购"时建议量归零，避免"无需采购/禁止采购却给建议量"的矛盾
        if trigger.get("purchase_trigger") != "需要采购" and suggested_qty > 0:
            suggested_qty = 0
            suggest_note = (suggest_note + "；触发非需要采购，建议量置0").strip()
        recorder.record(8, "建议采购数量计算", {
            "suggested_qty": suggested_qty,
            "forecast_total": forecast.get("forecast_total"),
            "available_stock": inventory.get("available_stock"),
            "first_month_forecast_qty": inventory.get("first_month_forecast_qty"),
            "arrival_days": inventory.get("arrival_days"),
            "sellable_gap_qty": inventory.get("sellable_gap_qty"),
            "gap_exempt": inventory.get("gap_exempt"),
            "在途预计上架日": inventory.get("inbound_arrival_date"),
            "在途预计上架日口径": inventory.get("inbound_arrival_formula"),
            "strategy_note": suggest_note,
        }, input_data={
            "forecast_total": forecast.get("forecast_total"),
            "forecast_months": forecast.get("forecast_months"),
            "available_stock": inventory.get("available_stock"),
            "inventory_days": inventory.get("inventory_days"),
            "replenishment_cycle": inventory.get("replenishment_cycle"),
            "festival": product.festival,
            "first_month_forecast_qty": inventory.get("first_month_forecast_qty"),
            "arrival_days": inventory.get("arrival_days"),
            "sellable_gap_qty": inventory.get("sellable_gap_qty"),
        }, reason=suggest_note or f"建议数量={suggested_qty}（预测未来销量−可使用库存，向上取整至箱规倍数）")

        # ── Step 9: 规划批次 ──
        # 长期老品：规则版沿用默认分批，各月占比与当月预测供 Step 15 AI 分割批次（Q8）
        batch_plan = _plan_batches(suggested_qty, product, lead_time, festival_window)
        _b9_input = {"suggested_qty": suggested_qty}
        _b9_reason = f"拆分为{len(batch_plan.get('batches', []))}批次"
        if inventory.get("long_term_window"):
            _b9_input["长期窗口"] = inventory["long_term_window"]
            _b9_reason += "（长期产品：各月占比与当月预测销量供 AI 分割批次）"
        recorder.record(9, "采购批次规划", batch_plan,
                        input_data=_b9_input, reason=_b9_reason)

        # ── Step 10: 计算评分（结合时间轴） ──
        scoring = _calc_score(forecast, inventory, life_cycle, trigger, product, purchase_window, lifecycle_end, False)
        recorder.record(10, "采购评分模型", {
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

    # ── 库存充足不加订（no_replenish_stock_cap 阈值规则，已按需求禁用）
    #    已禁用：不再因 FBA可售+在途 ≥ 阈值而直接不加订，改由特例规则/公式评分/AI 判定。
    #    _cap_hit 恒为 False，确保后续三裁判仲裁不会被该阈值硬拦截。
    _cap_hit = False

    # ── Step 11: 特例加订/不加订判定（人工业务规则《加订不加订理由》，新老品通用） ──
    from app.services.special_order_rules import (
        evaluate_special_orders, ADD_SCORE, BLOCK_SCORE,
    )

    acos_val = await _fetch_acos(product)
    daily_orders = await _recent_daily_orders(asin, session, days=7)
    # 特例不加订情况1：产品剩余可售卖时间 = 节日结束时间 − 当前时间 − 缓存天数
    festival_end_date = None
    if product.festival and (getattr(product, "product_type", "") or "") != "长期产品":
        _fi = await get_festival_info(product.festival, session)
        festival_end_date = (_fi or {}).get("festival_end")
    _buffer_days = buffer_days(await get_semantic_classification(session, asin))
    special = evaluate_special_orders(
        product=product,
        inventory=inventory,
        acos=acos_val,
        daily_orders=daily_orders,
        history=history,
        lead_time=lead_time,
        current_date=calc_date,
        festival_end=festival_end_date,
        buffer_days=_buffer_days,
    )
    special_applied = False
    special_ai_approved = False
    base_score_raw = scoring.get("purchase_score")
    base_score = base_score_raw or 0
    # 三裁判之「规则裁判」：特例/AI 介入前的公式判定
    rule_verdict = _normalize_verdict(scoring.get("purchase_level"))
    if special["add_cases"]:
        # 过季/无法赶上销售窗口：特例加订不生效（销售窗口已过，即使连续出单也不能补货）
        if not purchase_window.get("can_purchase", True):
            scoring.setdefault("score_detail", {})["特例加订拦截"] = {
                "name": "过季窗口拦截",
                "detail": (f"销售时间窗口已过（{purchase_window.get('reason', '')}），"
                           f"即使命中特例加订[{special['add_label'] or ''}]也不补货"),
            }
            special["add_cases"] = []
            special["add_label"] = None
        # 特例加订：先给候选分（85），最终是否保留由 AI 多方面分析确认（值不值得）
        if special["add_cases"]:
            scoring["purchase_score"] = max(base_score, ADD_SCORE)
            scoring["purchase_level"] = "立即采购" if not is_new_product else "建议采购"
            trigger["purchase_trigger"] = "需要采购"
            # 建议量：先按规则需求兜底，AI 评估后以 AI 精确建议量为准
            if not suggested_qty or suggested_qty <= 0:
                months = forecast.get("forecast_months") or []
                one_month = (months[0].get("forecast_qty") if months else 0) or round((forecast.get("forecast_total") or 0) / 6)
                if one_month <= 0:
                    daily = max(
                        float(inventory.get("recent_3_days_avg") or 0),
                        float(inventory.get("average_thirty_volume") or 0),
                        float(inventory.get("thirty_volume") or 0) / 30.0,
                    )
                    one_month = round(daily * 30)
                box_qty = _safe_int(product.box_quantity, 1)
                suggested_qty = new_product_policy.round_to_box(one_month, box_qty) if one_month > 0 else box_qty
            if not batch_plan or not batch_plan.get("batches"):
                batch_plan = _plan_batches(suggested_qty, product, lead_time)
            scoring.setdefault("score_detail", {})["特例加订"] = special["add_cases"]
            special_applied = True
    if special["block_cases"]:
        # 特例不加订：封顶分数阻止升级（即使公式评分高也不加订）
        scoring["purchase_score"] = min(scoring.get("purchase_score") or 0, BLOCK_SCORE)
        scoring["purchase_level"] = "暂停" if not is_new_product else "终止"
        trigger["purchase_trigger"] = "无需采购"
        suggested_qty = 0
        scoring.setdefault("score_detail", {})["特例不加订"] = special["block_cases"]
        special_applied = True
    # 三裁判之「特例裁判」：加订→采购 / 不加订→暂停 / 无特例→弃权
    special_verdict = None
    if special["add_cases"]:
        special_verdict = "采购"
    elif special["block_cases"]:
        special_verdict = "暂停"
    if special_applied:
        recorder.record(11, "特例加订判定", {
            "purchase_score": scoring.get("purchase_score"),
            "base_score": round(base_score, 2) if base_score else None,
            "purchase_level": scoring.get("purchase_level"),
            "特例加订": special["add_label"],
            "特例不加订": special["block_label"],
            "suggested_qty": suggested_qty,
        }, input_data={
            "acos": acos_val,
            "daily_orders_7d": daily_orders,
            "history": history,
        }, reason=(special["add_label"] or special["block_label"] or "无特例命中"))

    # ── AI 综合评估（新老品通用；仅对需要决策的产品，当天已评估则跳过） ──
    ai_analysis = None
    if ai_eval.ai_enabled():
        from app.services.config_service import get_param

        auto_ai = int(await get_param(session, "ai_auto_evaluate") or 1) == 1
        # 仅对“可执行决策”自动AI评估（立即采购/观察/新品建议采购/有建议数量）；
        # 纯“终止”产品用规则原因展示，需要时可手动AI评估，避免批量时成本过高
        candidate = (suggested_qty or 0) > 0 or (scoring.get("purchase_level") or "") in (
            "立即采购", "观察", "建议采购",
        )
        if auto_ai and candidate:
            already = (await session.execute(
                select(AiEvaluation.id, AiEvaluation.output_data).where(
                    AiEvaluation.asin == asin,
                    AiEvaluation.calc_date == calc_date,
                    AiEvaluation.eval_type == "purchase_advice",
                ).limit(1)
            )).first()
            # 特例加订：即使当天已评估也要重新评估（AI 复核值不值得，数量精确化）
            if already is None or special_applied:
                ai_analysis = await ai_eval.evaluate_purchase(asin, session)
            elif already:
                # 当天已评估：直接复用已落库结论（避免重跑重复调 AI，同时让否决逻辑生效）
                try:
                    _out = json.loads(already[1] or "{}") if already[1] else {}
                    _out = _out if isinstance(_out, dict) else {}
                    ai_analysis = {
                        "conclusion": _out.get("conclusion"),
                        "suggested_qty": _out.get("suggested_qty"),
                        "reason": _out.get("reason") or _out.get("error") or "",
                    }
                except (json.JSONDecodeError, TypeError):
                    ai_analysis = None
        # 特例加订：AI 多方面分析最终确认（值不值得 + 精确建议量）
        if special_applied and ai_analysis:
            ai_conclusion = (ai_analysis.get("conclusion") or "").strip()
            ai_qty = 0
            try:
                ai_qty = int(ai_analysis.get("suggested_qty") or 0)
            except (TypeError, ValueError):
                ai_qty = 0
            if ai_conclusion in ("暂停", "终止", "观察"):
                # AI 判定不值得加订（暂停/终止/观察=暂不补货）：取消特例，回退公式评分
                scoring["purchase_score"] = round(base_score, 2)
                scoring["purchase_level"] = {
                    "暂停": "暂停", "终止": "终止", "观察": "观察",
                }.get(ai_conclusion, "暂停")
                trigger["purchase_trigger"] = "无需采购"
                suggested_qty = 0
                batch_plan = {"batches": [], "total_qty": 0}
                scoring.setdefault("score_detail", {})["AI否决加订"] = {
                    "conclusion": ai_conclusion,
                    "reason": (ai_analysis.get("reason") or "")[:500],
                    "detail": "特例加订经AI多方面分析判定不值得，回退公式评分",
                }
            else:
                # AI 认可：保留加订（85分/立即采购），建议量以 AI 精确计算为准
                special_ai_approved = True
                if ai_qty > 0:
                    box_qty = _safe_int(product.box_quantity, 1)
                    suggested_qty = new_product_policy.round_to_box(ai_qty, box_qty)
                    batch_plan = _plan_batches(suggested_qty, product, lead_time)
                scoring.setdefault("score_detail", {})["特例加订建议量"] = {
                    "source": "AI综合评估",
                    "qty": suggested_qty,
                    "ai_qty": ai_qty,
                    "detail": "结合预测销量/库存覆盖天数/可用库存/补货周期/利润/生命周期等多因素，按AI建议量取整至箱规倍数",
                }
        # AI 综合评估否决（新老品通用）：门禁/规则建议采购，但 AI 判定 暂停/终止/观察 → 不采购
        if ai_analysis and not special_applied:
            ai_conclusion = (ai_analysis.get("conclusion") or "").strip()
            was_positive = ((suggested_qty or 0) > 0
                            or scoring.get("purchase_level") in ("立即采购", "建议采购"))
            if ai_conclusion in ("暂停", "终止", "观察") and was_positive:
                # AI 判定暂不补货（暂停/终止/观察）→ 统一列入「观察」
                scoring["purchase_level"] = "观察"
                if not is_new_product and base_score_raw:
                    scoring["purchase_score"] = base_score_raw
                trigger["purchase_trigger"] = "无需采购"
                suggested_qty = 0
                batch_plan = {"batches": [], "total_qty": 0}
                scoring.setdefault("score_detail", {})["AI否决采购"] = {
                    "conclusion": ai_conclusion,
                    "reason": (ai_analysis.get("reason") or "")[:500],
                    "detail": "AI 综合评估判定暂不补货，列入观察，否决门禁/规则的建议采购",
                }
        recorder.record(12, "AI综合评估", {
            "candidate": candidate,
            "ai_status": ai_analysis.get("status") if ai_analysis else "skipped",
            "ai_conclusion": ai_analysis.get("conclusion") if ai_analysis else None,
            "ai_suggested_qty": ai_analysis.get("suggested_qty") if ai_analysis else None,
            "final_suggested_qty": suggested_qty,
        }, input_data={"auto_evaluate": auto_ai, "purchase_level": scoring.get("purchase_level"),
                       "suggested_qty": suggested_qty},
           reason=(ai_analysis.get("reason") if ai_analysis else "AI未启用/非决策产品/当天已评估"))

    # 三裁判之「AI裁判」：AI 结论归一化（建议采购→采购 / 观察 / 暂停·终止→暂停）
    ai_verdict = None
    ai_qty = 0
    if ai_analysis:
        ai_verdict = _normalize_verdict((ai_analysis.get("conclusion") or "").strip())
        try:
            ai_qty = int(ai_analysis.get("suggested_qty") or 0)
        except (TypeError, ValueError):
            ai_qty = 0

    # 特例加订也必须遵循生命周期采购策略与库存规则：
    #   下降期 → 不采购；库存已覆盖生命周期窗口需求（缺口≤0）→ 不采购；
    #   建议量不超过生命周期窗口缺口（启动30天/增长60天/热卖90天/成熟按补货周期）
    _special_intercepted = False
    if special["add_cases"] and (suggested_qty or 0) > 0:
        # 与触发判断同一日销口径：最近3天平均 → 30天均值 → 快照日均，并乘生命周期系数
        _daily = 0.0
        _r3 = float(inventory.get("recent_3_days_avg") or 0)
        _t30 = float(inventory.get("thirty_volume") or 0) / 30.0
        _a30 = float(inventory.get("average_thirty_volume") or 0)
        if _r3 > 0:
            _daily = _r3
        elif _t30 > 0:
            _daily = _t30
        elif _a30 > 0:
            _daily = _a30
        # 生命周期日销系数取自 new_product_policy 唯一来源（可被 config_service 的
        # lifecycle_factors 覆盖），不再使用本模块内的硬编码副本
        _daily *= new_product_policy.LIFECYCLE_FACTORS.get(life_cycle, (1.0, 1.2))[0]
        _inv_days = trigger.get("inventory_days")
        if _inv_days is None:
            _inv_days = inventory.get("inventory_days")
        _cycle = int(inventory.get("replenishment_cycle") or trigger.get("replenishment_cycle") or 60)
        _window = {"启动期": 30, "增长期": 60, "热卖期": 90}.get(life_cycle, _cycle)
        if _inv_days is not None and _cycle and _inv_days >= _cycle:
            _gap = 0  # 库存已覆盖补货周期 → 无需采购
        else:
            _gap = max(0, round(_daily * _window)
                       - int(inventory.get("available_stock") or 0)
                       - int(inventory.get("local_stock") or 0)
                       - int(inventory.get("purchase_on_order") or 0))
        if life_cycle == "下降期" or _gap <= 0:
            _special_intercepted = True
            # 生命周期/库存规则拦截：取消特例加订，回退公式评分
            scoring["purchase_score"] = base_score_raw
            sd = scoring.get("score_detail")
            orig_level = sd.get("level") if isinstance(sd, dict) else None
            if orig_level:
                scoring["purchase_level"] = orig_level
            else:
                scoring["purchase_level"] = (
                    "立即采购" if base_score >= 80 else ("观察" if base_score >= 60 else "暂停")
                )
            trigger["purchase_trigger"] = "无需采购"
            suggested_qty = 0
            batch_plan = {"batches": [], "total_qty": 0}
            special_ai_approved = False
            if isinstance(sd, dict):
                sd["特例拦截"] = {
                    "name": "生命周期下降期不加订" if life_cycle == "下降期" else "库存覆盖周期不加订",
                    "detail": (
                        "生命周期下降期采购策略为减少采购/不采购，特例加订不生效"
                        if life_cycle == "下降期"
                        else f"库存可覆盖{life_cycle}采购窗口（缺口 {_gap}），特例加订也需遵循库存规则，不加订"
                    ),
                }
        else:
            # 建议量受生命周期采购策略与库存缺口上限约束
            capped = min(suggested_qty or 0, _gap)
            if capped != (suggested_qty or 0):
                suggested_qty = capped
                batch_plan = _plan_batches(suggested_qty, product, lead_time)
                scoring.setdefault("score_detail", {})["特例建议量约束"] = {
                    "qty": suggested_qty,
                    "gap": _gap,
                    "lifecycle": life_cycle,
                    "window_days": _window,
                    "detail": "特例加订建议量按生命周期采购策略/库存缺口上限约束",
                }

    # ── 第二高峰期补货判断：最后需要补货（成熟期触发需要采购）时 ──
    #    用户规则：补货后库存能临近第二个高峰期 → 允许提前补货；否则即使在成熟期也不补货。
    _second_peak_blocked = False
    if (not is_new_product and life_cycle == "成熟期"
            and trigger.get("purchase_trigger") == "需要采购" and (suggested_qty or 0) > 0):
        try:
            peak_check = await _check_second_peak_replenishment(
                product, inventory, suggested_qty, lead_time, purchase_window,
                life_cycle, calc_date, session,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{asin}] 第二高峰期判断失败，跳过该规则: {e}")
            peak_check = {"applicable": False, "detail": str(e)}
        if peak_check.get("applicable"):
            recorder.record(13, "第二高峰期判断", {
                "second_peak_start": str(peak_check.get("second_peak_start")) if peak_check.get("second_peak_start") else None,
                "coverage_until": str(peak_check.get("coverage_until")) if peak_check.get("coverage_until") else None,
                "allowed": peak_check.get("allowed"),
            }, input_data={
                "suggested_qty": suggested_qty,
                "available_stock": inventory.get("available_stock"),
                "purchase_on_order": inventory.get("purchase_on_order"),
                "recommended_transport": purchase_window.get("recommended_transport"),
            }, reason=peak_check.get("detail", ""))
            if peak_check.get("allowed"):
                scoring.setdefault("score_detail", {})["第二高峰期"] = {
                    "allowed": True,
                    "second_peak": str(peak_check.get("second_peak_start")),
                    "coverage_until": str(peak_check.get("coverage_until")),
                    "detail": peak_check.get("detail", "补货后可临近第二个高峰期，允许提前补货"),
                }
            else:
                _second_peak_blocked = True
                trigger["purchase_trigger"] = "无需采购"
                trigger["reason"] = (peak_check.get("detail") or "无第二个高峰期可覆盖，最后阶段不补货") \
                    + " | " + (trigger.get("reason") or "")
                suggested_qty = 0
                batch_plan = {"batches": [], "total_qty": 0}
                if scoring.get("purchase_level") in ("立即采购", "建议采购"):
                    scoring["purchase_level"] = "暂停"
                scoring.setdefault("score_detail", {})["第二高峰期拦截"] = {
                    "second_peak": str(peak_check.get("second_peak_start")) if peak_check.get("second_peak_start") else None,
                    "coverage_until": str(peak_check.get("coverage_until")) if peak_check.get("coverage_until") else None,
                    "detail": peak_check.get("detail", "补货后无法临近第二个高峰期，最后阶段不补货"),
                }

    # ── 硬规则拦截（级别高于三裁判）：命中即终止加订，跳过规则/特例/AI 仲裁 ──
    #    ① 成本表三渠道 Profit 全为负 → 停止加购
    #    ② 剩余售卖天数（库存可售天数）< 14 天 → 直接终止加订
    _hard_rule_hit = _check_hard_rules(product, inventory)
    if _hard_rule_hit:
        trigger["purchase_trigger"] = "终止加订"
        trigger["reason"] = f"{_hard_rule_hit['detail']} | {trigger.get('reason', '')}"
        suggested_qty = 0
        batch_plan = {"batches": [], "total_qty": 0}
        scoring["purchase_level"] = "终止"
        scoring.setdefault("score_detail", {})["硬规则拦截"] = _hard_rule_hit
        recorder.record(13, "硬规则拦截（高于三裁判）", {
            "rule": _hard_rule_hit["rule"],
            "inventory_days": inventory.get("inventory_days"),
        }, input_data={"asin": asin, "inventory_days": inventory.get("inventory_days")},
           reason=_hard_rule_hit["detail"], status="failed")

    # ── 长期产品库存充足拦截（新老品统一；级别高于评分>80/特例加订/三裁判/AI） ──
    #    长期产品「预测未来销量 ≤ 可用库存」→ 强制不采购
    _long_term_block = (
        None if _hard_rule_hit else _check_long_term_stock_cover(forecast, inventory, product)
    )
    if _long_term_block:
        trigger["purchase_trigger"] = "无需采购"
        trigger["reason"] = f"{_long_term_block['detail']} | {trigger.get('reason', '')}"
        suggested_qty = 0
        batch_plan = {"batches": [], "total_qty": 0}
        # 仅在当前等级并非更强的拦截等级（终止/暂停）时降为观察
        if scoring.get("purchase_level") not in ("终止", "暂停"):
            scoring["purchase_level"] = "观察"
        scoring.setdefault("score_detail", {})["长期产品库存充足拦截"] = _long_term_block
        recorder.record(13, "长期产品库存充足拦截（高于三裁判）", {
            "rule": _long_term_block["rule"],
            "forecast_total": _long_term_block["forecast_total"],
            "available_stock": _long_term_block["available_stock"],
        }, input_data={"asin": asin, "product_type": getattr(product, "product_type", None)},
           reason=_long_term_block["detail"], status="failed")

    # ── 三裁判仲裁：规则/特例/AI 两票一致即定，三方都不同规则裁判为准 ──
    #    硬拦截（过季/新品门禁/库存上限/下降期/特例拦截/第二高峰/硬规则/长期产品库存充足）在仲裁之上，命中则跳过仲裁。
    hard_blocked = (
        not purchase_window.get("can_purchase", True)
        or trigger.get("purchase_trigger") in ("禁止采购", "终止加订")
        or _cap_hit or _special_intercepted or _second_peak_blocked or _hard_rule_hit
        or _long_term_block
        or scoring.get("purchase_level") == "终止"
    )
    if ai_verdict and not hard_blocked:
        final_verdict, arb_reason, arb_votes = _arbitrate_verdicts(
            rule_verdict, special_verdict, ai_verdict,
        )
        scoring.setdefault("score_detail", {})["三裁判仲裁"] = {
            "规则裁判": rule_verdict,
            "特例裁判": special_verdict,
            "AI裁判": ai_verdict,
            "最终判定": final_verdict,
            "依据": arb_reason,
        }
        recorder.record(14, "三裁判仲裁", {
            "规则裁判": rule_verdict,
            "特例裁判": special_verdict,
            "AI裁判": ai_verdict,
            "最终判定": final_verdict,
        }, input_data={"votes": [f"{j}={v}" for j, v in arb_votes]}, reason=arb_reason)
        if final_verdict == "采购":
            scoring["purchase_level"] = "立即采购" if not is_new_product else "建议采购"
            trigger["purchase_trigger"] = "需要采购"
            if ai_verdict == "采购" and ai_qty > 0:
                suggested_qty = new_product_policy.round_to_box(
                    ai_qty, _safe_int(product.box_quantity, 1))
                batch_plan = _plan_batches(suggested_qty, product, lead_time)
            if (suggested_qty or 0) <= 0:
                # 采购但建议量为0 → 用规则口径兜底（未来1个月需求），仍为0则降为观察
                months = forecast.get("forecast_months") or []
                one_month = (months[0].get("forecast_qty") if months else 0) \
                    or round((forecast.get("forecast_total") or 0) / 6)
                box_qty = _safe_int(product.box_quantity, 1)
                if one_month > 0:
                    suggested_qty = new_product_policy.round_to_box(one_month, box_qty)
                    batch_plan = _plan_batches(suggested_qty, product, lead_time)
            if (suggested_qty or 0) <= 0:
                final_verdict = "观察"
                scoring["purchase_level"] = "观察"
                trigger["purchase_trigger"] = "无需采购"
        elif final_verdict == "观察":
            scoring["purchase_level"] = "观察"
            trigger["purchase_trigger"] = "无需采购"
            suggested_qty = 0
            batch_plan = {"batches": [], "total_qty": 0}
        else:  # 暂停
            scoring["purchase_level"] = "暂停" if not is_new_product else "终止"
            trigger["purchase_trigger"] = "无需采购"
            suggested_qty = 0
            batch_plan = {"batches": [], "total_qty": 0}

    # L2：等级为暂停/终止且非特例加订AI认可 → 建议量归零，避免“暂停却建议采购N件”矛盾
    if scoring.get("purchase_level") in ("暂停", "终止") and not special_ai_approved and (suggested_qty or 0) > 0:
        suggested_qty = 0
        batch_plan = {"batches": [], "total_qty": 0}

    # 标签一致性：触发非需要采购 或 建议量=0 → 不允许保留“立即采购/建议采购”标签，校正为观察
    if (trigger.get("purchase_trigger") != "需要采购" or (suggested_qty or 0) <= 0) \
            and scoring.get("purchase_level") in ("立即采购", "建议采购"):
        scoring["purchase_level"] = "观察"
        scoring.setdefault("score_detail", {})["标签校正"] = {
            "from": "立即采购/建议采购",
            "to": "观察",
            "detail": "触发非需要采购或建议量为0，校正为观察，避免“建议采购但0件”矛盾",
        }

    # 特例加订/不加订、AI 否决覆盖后，同步 score_detail 的最终等级/评分/建议量，
    # 避免"决策等级：建议采购"但分数55/暂停的展示矛盾
    if isinstance(scoring.get("score_detail"), dict):
        sd = scoring["score_detail"]
        if sd.get("level") is not None and sd.get("level") != scoring.get("purchase_level"):
            sd["原始等级"] = sd["level"]
            sd["level"] = scoring.get("purchase_level")
            sd["最终等级"] = scoring.get("purchase_level")
        if sd.get("suggested_qty") is not None:
            sd["suggested_qty"] = suggested_qty

    # ── 到货日期 vs 生命周期结束日期：最后判断需要采购的产品，若到货后已过该生命周期阶段结束 → 不采购 ──
    if trigger.get("purchase_trigger") == "需要采购" and (suggested_qty or 0) > 0:
        if lifecycle_end:
            stage_name, end_date = lifecycle_end_info
            batches = (batch_plan or {}).get("batches") or []
            arrival_days = batches[0].get("days") if batches else (lead_time or 30) + 45
            arrival_date = calc_date + timedelta(days=int(arrival_days or 0))
            if arrival_date > end_date:
                trigger["purchase_trigger"] = "无需采购"
                trigger["reason"] = (
                    f"到货日期{arrival_date}超过销售窗口结束日期{end_date}（{stage_name}），"
                    f"无法赶上销售窗口，不采购 | {trigger.get('reason', '')}"
                )
                suggested_qty = 0
                batch_plan = {"batches": [], "total_qty": 0}
                if scoring.get("purchase_level") in ("立即采购", "建议采购"):
                    scoring["purchase_level"] = "暂停" if not is_new_product else "终止"
                    if not is_new_product and base_score_raw:
                        scoring["purchase_score"] = base_score_raw
                scoring.setdefault("score_detail", {})["到货日期拦截"] = {
                    "lifecycle": stage_name,
                    "lifecycle_end": end_date.isoformat(),
                    "arrival_date": arrival_date.isoformat(),
                    "detail": "到货后已超过销售窗口结束时间，不采购",
                }

    # ── Step 15: AI 分批补货规划（确认采购后，基于多因素给出各批数量与下单/到货日期） ──
    #    仅在最终确认「需要采购」且建议量>0 时调用；AI 不可用/失败时沿用规则版批次规划。
    if trigger.get("purchase_trigger") == "需要采购" and (suggested_qty or 0) > 0 and ai_eval.ai_enabled():
        _batch_ctx = await _build_batch_ai_context(
            asin, product, suggested_qty, lead_time, inventory, forecast, life_cycle, session)
        try:
            _ai_batch = await ai_eval.plan_batches_ai(asin, suggested_qty, _batch_ctx, session)
            if _ai_batch.get("status") == "success" and _ai_batch.get("batches"):
                batch_plan = {
                    "batches": _ai_batch["batches"],
                    "total_qty": suggested_qty,
                    "source": "ai",
                    "summary": _ai_batch.get("summary"),
                }
                recorder.record(15, "AI分批补货计划", {
                    "batches": _ai_batch["batches"],
                    "summary": _ai_batch.get("summary"),
                    "confidence": _ai_batch.get("confidence"),
                }, input_data=_batch_ctx,
                   reason=f"AI拆分为{len(_ai_batch['batches'])}批（含下单/到货日期）：{_format_batch_plan(batch_plan)}")
            else:
                recorder.record(15, "AI分批补货计划", {
                    "status": _ai_batch.get("status"),
                    "fallback": "规则版批次规划",
                }, input_data=_batch_ctx, reason="AI不可用/失败，沿用规则版批次规划")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{asin}] AI分批补货规划异常，沿用规则批次: {e}")

    # 新品补分：门禁给出决策（建议采购/观察）时，用六维加权总分作为采购评分，
    # 保证详情页/日报/导出都有分数（未触发/终止保持无分）
    if is_new_product and scoring.get("purchase_level") in ("建议采购", "观察") \
            and scoring.get("purchase_score") is None:
        _six_total = _six_dim_total(scoring.get("score_detail") or {})
        if _six_total is not None:
            scoring["purchase_score"] = _six_total

    # ── 存储计算结果 ──
    # 修复：节日窗口为触发"需要采购"把库存天数置0；若最终决策被降级为观察/暂停/终止，
    #    还原为真实库存覆盖天数，避免日报"库存0天"与库存充足的矛盾展示
    _raw_days = inventory.get("_raw_inventory_days")
    if _raw_days is not None and scoring.get("purchase_level") in ("观察", "暂停", "终止"):
        inventory["inventory_days"] = _raw_days
    calc_result = CalculationResult(
        asin=asin,
        calc_date=calc_date,
        logic_version=1,
        product_stage=product.product_stage or _computed_stage,
        product_level=get_product_level(product),
        life_cycle=life_cycle,
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
        base_score=round(base_score, 2) if base_score else None,
        purchase_level=scoring.get("purchase_level"),
        score_detail=json.dumps(scoring.get("score_detail", {}), ensure_ascii=False),
        # 领星口径交叉验证（来自最新库存快照，与公式结果互验）
        lx_available_days=inventory.get("fba_available_days"),
        lx_stockout_date=inventory.get("stockout_date"),
        lx_estimated_daily_sales=inventory.get("estimated_daily_sales"),
    )
    session.add(calc_result)
    await session.flush()

    # 将步骤记录持久化（绑定 calculation_id）
    recorder.save(calc_result.id)

    logger.info("计算结果已存储 id=%s", calc_result.id)

    # 成本表（三渠道 Profit）+ 盈利结论，口径与详情页/成本表接口一致
    cost_table = None
    cost_ok, cost_reason = None, ""
    try:
        _cost_cfg = await _get_new_product_cfg(session)
        cost_table = new_product_policy.calc_cost_table(product, cfg=_cost_cfg)
        cost_ok, cost_reason = new_product_policy.check_cost_table(cost_table)
    except Exception:
        cost_table = None

    # 历史对比趋势（按日期升序，含本次结果，供私发报告展示）
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

    # ── 返回结果（含每步溯源） ──
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
        "ai_analysis": ai_analysis,
        "reason": trigger.get("reason", ""),
        "cost_table": cost_table,
        "cost_check": {"ok": cost_ok, "reason": cost_reason},
        "history": history,
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
    logger.info("计算完成 ASIN=%s score=%s level=%s", asin, scoring.get("purchase_score"), scoring.get("purchase_level"))
    return result_dict


async def run_due_calculation(force: bool = False, progress: dict | None = None) -> dict:
    """按等级频率计算到期产品（S 每天、A 每3天、B 每5天、C 每7天、D 每14天，可自定义）

    force=True 时忽略频率，全量重算所有启用产品。
    progress: 可选进度字典 {"total","done","percent","current_asin"}，用于接口实时返回进度。
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
                last_dates = await _load_effective_last_dates(session)
            freq_map, default_freq = await _load_frequency_config(session)

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

            # 第一遍：统计各等级产品数，并筛选出到期产品（用于进度 total）
            due_products: list = []
            for product in products:
                level = get_product_level(product)
                if level not in stats["by_level"]:
                    stats["by_level"][level] = {
                        "total": 0,
                        "due": 0,
                        "success": 0,
                        "failed": 0,
                        "frequency_days": get_calculation_frequency_days(product, freq_map, default_freq),
                    }
                stats["by_level"][level]["total"] += 1

                if force:
                    due = True
                else:
                    freq = get_calculation_frequency_days(product, freq_map, default_freq)
                    last = last_dates.get(product.asin)
                    due = is_calculation_due(last, today, freq)

                if not due:
                    stats["skipped"] += 1
                    continue
                due_products.append(product)

            # 日报分析生命周期自定义过滤：仅按频率模式生效，空配置=全部生命周期不过滤
            if not force:
                allowed_lifecycles = await _load_report_lifecycles(session)
                if allowed_lifecycles:
                    before = len(due_products)
                    due_products = [
                        p for p in due_products
                        if (p.life_cycle or "未知").strip() in allowed_lifecycles
                    ]
                    filtered = before - len(due_products)
                    stats["skipped"] += filtered
                    logger.info(
                        "日报生命周期过滤：允许 %s，剔除 %d 个到期产品",
                        sorted(allowed_lifecycles), filtered,
                    )

            if progress is not None:
                progress["total"] = len(due_products)
                progress["done"] = 0
                progress["percent"] = 0
                progress["current_asin"] = None

            # 第二遍：并发计算到期产品（默认5路，每产品独立 session），实时更新进度
            sem = asyncio.Semaphore(5)
            stats["due"] = len(due_products)
            for level, lv in stats["by_level"].items():
                lv["due"] = sum(1 for p in due_products if get_product_level(p) == level)
            done = 0

            async def _calc_one(product, level):
                nonlocal done
                async with sem:
                    async with async_session_factory() as s:
                        try:
                            calc_result = await run_single_calculation(product.asin, s)
                            await s.commit()
                            skipped = bool(calc_result.get("skipped"))
                            failed = (not skipped) and "error" in calc_result
                            error = calc_result.get("error") if failed else None
                        except Exception as e:  # noqa: BLE001
                            await s.rollback()
                            failed, error = True, str(e)
                            skipped = False
                            logger.error("计算失败 ASIN=%s error=%s", product.asin, e)
                    done += 1
                    if progress is not None:
                        progress["done"] = done
                        progress["percent"] = round(done / max(len(due_products), 1) * 100)
                        progress["current_asin"] = product.asin
                    if skipped:
                        stats["skipped"] += 1
                    elif failed:
                        stats["failed"] += 1
                        stats["by_level"][level]["failed"] += 1
                        stats["errors"].append({"asin": product.asin, "error": str(error)})
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

            tasks = [asyncio.ensure_future(_calc_one(p, get_product_level(p))) for p in due_products]
            if tasks:
                await asyncio.gather(*tasks)

            await session.commit()
            logger.info(
                "===== %s计算任务完成: 总计=%d, 到期=%d, 跳过=%d, 成功=%d, 失败=%d, "
                "立即采购=%d, 观察=%d, 暂停=%d =====",
                mode, stats["total"], stats["due"], stats["skipped"],
                stats["success"], stats["failed"],
                stats["immediate"], stats["observe"], stats["pause"],
            )
            # 计算过程产生的原始接口响应统一落库
            try:
                from app.services.raw_store import flush_raw

                await flush_raw()
            except Exception:  # noqa: BLE001
                pass
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


async def run_batch_calculation(progress: dict | None = None):
    """对所有启用产品执行全量批量计算（等价 run_due_calculation(force=True)）"""
    return await run_due_calculation(force=True, progress=progress)


async def run_level_calculation(level: str, progress: dict | None = None,
                                lifecycles: str | list[str] | set[str] | None = None) -> dict:
    """立即计算指定等级（S/A/B/C/D）的全部启用产品，忽略频率
    支持多等级："SAB" 或 "S,A,B"（API / 脚本均可传）。
    lifecycles: 可选生命周期过滤（如 "启动期,增长期,热卖期"），None/空=不限制
    progress: 可选进度字典 {"total","done","percent","current_asin"}
    """
    levels = _normalize_levels(level)
    if not levels:
        logger.warning("按等级计算：未识别到有效等级（%s），跳过", level)
        return {"total": 0, "due": 0, "skipped": 0, "success": 0, "failed": 0,
                "immediate": 0, "observe": 0, "pause": 0, "by_level": {},
                "errors": [f"无效产品等级: {level}"]}
    logger.info("===== 按等级(%s)立即计算开始 =====", "/".join(levels))
    session = async_session_factory()
    try:
        async with session:
            result = await session.execute(
                select(Product).where(Product.status == True, Product.product_level.in_(levels))  # noqa: E712
            )
            products = result.scalars().all()
            lc_filter = _normalize_lifecycles(lifecycles)
            if lc_filter:
                before = len(products)
                products = [p for p in products if (p.life_cycle or "未知").strip() in lc_filter]
                logger.info("按等级计算生命周期过滤：允许 %s，剔除 %d 个产品", sorted(lc_filter), before - len(products))
            if progress is not None:
                progress["total"] = len(products)
                progress["done"] = 0
                progress["percent"] = 0
                progress["current_asin"] = None
            stats = {
                "total": len(products),
                "due": len(products),
                "skipped": 0,
                "success": 0,
                "failed": 0,
                "immediate": 0,
                "observe": 0,
                "pause": 0,
                "by_level": {lv: {"total": 0, "due": 0, "success": 0, "failed": 0,
                                  "frequency_days": 1} for lv in levels},
                "errors": [],
            }
            for p in products:
                lv = (p.product_level or "").upper()
                if lv in stats["by_level"]:
                    stats["by_level"][lv]["total"] += 1
                    stats["by_level"][lv]["due"] += 1
            # 并发计算（默认5路，每产品独立 session）
            sem = asyncio.Semaphore(5)
            done = 0

            async def _calc_one(product):
                nonlocal done
                async with sem:
                    async with async_session_factory() as s:
                        try:
                            calc_result = await run_single_calculation(product.asin, s)
                            await s.commit()
                            failed = "error" in calc_result
                            error = calc_result.get("error") if failed else None
                        except Exception as e:  # noqa: BLE001
                            await s.rollback()
                            failed, error = True, str(e)
                            logger.error("计算失败 ASIN=%s error=%s", product.asin, e)
                    done += 1
                    if progress is not None:
                        progress["done"] = done
                        progress["percent"] = round(done / max(len(products), 1) * 100)
                        progress["current_asin"] = product.asin
                    lv = (product.product_level or "").upper()
                    if failed:
                        stats["failed"] += 1
                        if lv in stats["by_level"]:
                            stats["by_level"][lv]["failed"] += 1
                        stats["errors"].append({"asin": product.asin, "error": str(error)})
                    else:
                        stats["success"] += 1
                        if lv in stats["by_level"]:
                            stats["by_level"][lv]["success"] += 1
                        pl = calc_result.get("purchase_level", "")
                        if pl == "立即采购":
                            stats["immediate"] += 1
                        elif pl == "观察":
                            stats["observe"] += 1
                        elif pl == "暂停":
                            stats["pause"] += 1

            tasks = [asyncio.ensure_future(_calc_one(p)) for p in products]
            if tasks:
                await asyncio.gather(*tasks)
            await session.commit()
            logger.info("===== %s 级计算完成: 成功=%d 失败=%d =====",
                        "/".join(levels), stats["success"], stats["failed"])
            return stats
    except Exception as e:
        logger.error("%s 级计算异常: %s", "/".join(levels), e)
        return {"total": 0, "due": 0, "skipped": 0, "success": 0, "failed": 0,
                "immediate": 0, "observe": 0, "pause": 0, "by_level": {}, "errors": [str(e)]}
    finally:
        await session.close()


async def get_due_calculation_stats(session: AsyncSession) -> dict:
    """统计各等级产品数量与今日到期情况（不执行计算，用于预览/接口）"""
    result = await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )
    products = result.scalars().all()
    last_dates = await _load_effective_last_dates(session)
    freq_map, default_freq = await _load_frequency_config(session)
    today = date.today()

    by_level: dict[str, dict] = {}
    for product in products:
        level = get_product_level(product)
        freq = get_calculation_frequency_days(product, freq_map, default_freq)
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


async def get_next_calculation_info(session: AsyncSession) -> dict:
    """计算下一次自动分析的时间与涉及的产品等级（不执行计算，供仪表盘预告）

    下一次分析时间 = 全量产品中最早到期的下次计算日期；
    下一次分析等级 = 在该日期到期的所有产品等级（按 S/A/B/C/D 排序去重）。
    """
    result = await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )
    products = result.scalars().all()
    last_dates = await _load_effective_last_dates(session)
    freq_map, default_freq = await _load_frequency_config(session)
    today = date.today()

    next_by_product: list[tuple[date, str]] = []
    for product in products:
        level = get_product_level(product)
        freq = get_calculation_frequency_days(product, freq_map, default_freq)
        last = last_dates.get(product.asin)
        if last is None or (today - last).days >= freq:
            next_date = today
        else:
            next_date = last + timedelta(days=freq)
        next_by_product.append((next_date, level))

    if not next_by_product:
        return {"next_date": None, "next_levels": [], "due_count": 0, "today": today.isoformat()}

    earliest = min(d for d, _ in next_by_product)
    next_levels = sorted({lv for d, lv in next_by_product if d == earliest})
    due_count = sum(1 for d, _ in next_by_product if d == earliest)
    return {
        "next_date": earliest.isoformat(),
        "next_levels": next_levels,
        "next_levels_text": "+".join(next_levels) if next_levels else "暂无",
        "due_count": due_count,
        "today": today.isoformat(),
    }


