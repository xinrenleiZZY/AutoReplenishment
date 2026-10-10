"""评分与批次规划（C1 拆分批1 · score 域 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
对外入口不变：app.tasks.calculation_tasks.<name> 仍可用（原文件 star-import 本模块）。
"""

import json
from datetime import date, timedelta
from math import ceil
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product import Product
from app.services import new_product_policy
from app.services.calc.common import (  # noqa: F401
    _parse_step_json,
    _safe_float,
    _safe_int,
    _to_float_safe,
)


def _format_score_detail(detail) -> str:
    """评分明细转可读文本：老品六维评分 / 新品决策原因 / 原样 JSON"""
    if not detail:
        return ""
    if isinstance(detail, dict):
        labels = {
            "shortage_score": "断货风险",
            "trend_score": "销量趋势",
            "profit_score": "利润空间",
            "life_score": "生命周期",
            "urgency": "库存紧急",
            "transport_score": "运输可达",
        }
        parts = []
        for key, label in labels.items():
            item = detail.get(key)
            if isinstance(item, dict) and item.get("value") is not None:
                parts.append(f"{label}{item['value']}")
        if parts:
            return "·".join(parts)
        reason = detail.get("reason")
        if reason:
            return str(reason)
    return json.dumps(detail, ensure_ascii=False) if detail else ""


def _six_dim_total(score_detail) -> float | None:
    """新品六维评分加权总分（无 purchase_score 时的展示评分）"""
    if not isinstance(score_detail, dict):
        return None
    dims = score_detail.get("六维评分")
    if not isinstance(dims, dict):
        dims = score_detail
    total = 0.0
    found = 0
    for key in ("shortage_score", "trend_score", "profit_score", "life_score", "urgency", "transport_score"):
        item = dims.get(key)
        if isinstance(item, dict):
            try:
                total += float(item.get("value", 0)) * float(item.get("weight", 0))
                found += 1
            except (ValueError, TypeError):
                pass
    return round(total, 2) if found else None


def _format_batch_plan(plan) -> str:
    """批次规划转可读文本，如：1.空运500件(55天)；2.海运1200件(75天)

    含下单/到货日期时（AI 分批）展示为：1.空运500件(下单2026-09-15/到货2026-10-20)
    """
    if not plan or not isinstance(plan, dict):
        return ""
    batches = plan.get("batches") or []
    parts = []
    for b in batches:
        if not isinstance(b, dict):
            continue
        method = b.get("method", "")
        qty = b.get("qty", 0)
        days = b.get("days")
        order_date = b.get("order_date")
        arrival_date = b.get("arrival_date")
        part = f"{b.get('batch_no')}.{method}{qty}件"
        if order_date or arrival_date:
            part += f"(下单{order_date or '?'}/到货{arrival_date or '?'})"
        elif days:
            part += f"({days}天)"
        parts.append(part)
    return "；".join(parts)


async def _build_batch_ai_context(asin: str, product: Product, suggested_qty: int, lead_time: int,
                                  inventory: dict, forecast: dict, life_cycle: str,
                                  session: AsyncSession) -> dict:
    """组装 AI 分批补货规划的多因素输入（产品档案/库存/预测/运输时效与费用/节日窗口/生命周期）"""
    from app.services.config_service import get_param

    async def g(key, default):
        v = await get_param(session, key)
        return v if v is not None else default

    is_peak = 8 <= date.today().month <= 12
    season = "旺季" if is_peak else "淡季"
    transport_modes = [
        {
            "方式": "海运",
            "时效(天)": settings.SEA_PEAK_DAYS if is_peak else settings.SEA_SLOW_DAYS,
            "费用(元/件)": float(await g("sea_peak_fee" if is_peak else "sea_slow_fee",
                                        settings.SEA_PEAK_FEE if is_peak else settings.SEA_SLOW_FEE)),
        },
        {
            "方式": "空派",
            "时效(天)": settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS,
            "费用(元/件)": float(await g("air_peak_fee" if is_peak else "air_slow_fee",
                                        settings.AIR_PEAK_FEE if is_peak else settings.AIR_SLOW_FEE)),
        },
        {
            "方式": "快递",
            "时效(天)": settings.EXPRESS_PEAK_DAYS if is_peak else settings.EXPRESS_SLOW_DAYS,
            "费用(元/件)": float(await g("express_peak_fee" if is_peak else "express_slow_fee",
                                        settings.EXPRESS_PEAK_FEE if is_peak else settings.EXPRESS_SLOW_FEE)),
        },
    ]
    return {
        "asin": asin,
        "产品名称": product.product_name,
        "补货总量X": suggested_qty,
        "箱规": _safe_int(product.box_quantity, 1),
        "生命周期": life_cycle,
        "产品类型": "节日产品" if product.festival else "长期产品",
        "节日": product.festival,
        "今天": date.today().isoformat(),
        "大货工期(天)": lead_time,
        "运输季节": season,
        "运输方式可选": transport_modes,
        "补货周期(天)": inventory.get("replenishment_cycle"),
        "库存可售天数": inventory.get("inventory_days"),
        "可用库存": inventory.get("available_stock"),
        "在途合计": inventory.get("inbound_total"),
        "预测总销量": forecast.get("forecast_total"),
        "未来月度预测": forecast.get("forecast_months"),
        "首月预测销量(库存口径)": inventory.get("first_month_forecast_qty"),
        "节日窗口": inventory.get("festival_window"),
        # 老品-长期产品：去年各月销量占比 + 今年当月预测销量（Q8，供 AI 分割批次）
        "长期窗口": inventory.get("long_term_window"),
    }


def _plan_batches(suggested_qty: int, product: Product, lead_time: int, festival_window: dict | None = None) -> dict:
    """规划采购批次

    节日产品（festival_window 非空）：第1批保热卖月（空运，赶在热卖月前上架），
    第2批保后续窗口月份（海运）；热卖月需求=窗口各月预估中 ≤ hot_end_month 的月份之和。
    非节日/普通产品：沿用简单分批（急单空运 30% + 常规海运 70%）或单批。
    """
    if suggested_qty <= 0:
        return {"batches": [], "total_qty": 0}

    box_qty = _safe_int(product.box_quantity, 1)
    is_peak = 8 <= date.today().month <= 12
    air_days = settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS
    sea_days = settings.SEA_PEAK_DAYS if is_peak else settings.SEA_SLOW_DAYS

    # ── 节日分批次：第1批=热卖月需求（空运），第2批=其余窗口需求（海运） ──
    if festival_window and suggested_qty > box_qty * 2:
        hot_end = int(festival_window.get("hot_end_month") or 0)
        month_est = festival_window.get("month_estimate") or {}
        if hot_end and month_est:
            hot_qty = ceil(
                sum(q for m, q in month_est.items() if int(m) <= hot_end) / box_qty
            ) * box_qty
            hot_qty = max(hot_qty // box_qty * box_qty, box_qty)  # 至少一箱
            if hot_qty >= suggested_qty:
                # 热卖月需求已覆盖/超出全部建议量：整批空运保热卖，不再拆海运（避免误分配到海运赶不上热卖）
                batches = [
                    {"batch_no": 1, "qty": suggested_qty, "method": "空运", "days": lead_time + air_days,
                     "protect": f"保护热卖月(≤{hot_end}月)"},
                ]
                return {"batches": batches, "total_qty": suggested_qty}
            if hot_qty > 0:
                batches = [
                    {"batch_no": 1, "qty": hot_qty, "method": "空运", "days": lead_time + air_days,
                     "protect": f"保护热卖月(≤{hot_end}月)"},
                    {"batch_no": 2, "qty": suggested_qty - hot_qty, "method": "海运", "days": lead_time + sea_days,
                     "protect": f"覆盖后续窗口月份"},
                ]
                return {"batches": batches, "total_qty": suggested_qty}

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
                trigger: dict, product: Product, purchase_window: dict = None,
                stage_end=None, is_new_product: bool = False,
                new_product_profit_score: Optional[int] = None) -> dict:
    """计算采购评分（新老品两套逻辑）

    新品（is_new_product=True）：六维评分 + SCORE_BALANCE_* 权重（默认1.0均分）；
        其中「利润空间」唯一取自 DeepSeek 综合评分 new_product_profit_score（见
        ai_eval.evaluate_new_product_profit_space），不再用毛利率线性公式，失败/缺省按 0，不回退。
    老品（is_new_product=False，默认）：按《老品采购评分指标》分档规则，权重 ①20%/②25%/③25%/④10%/⑤10%/⑥10%。
    """
    inventory_days = trigger.get("inventory_days")
    replenishment_cycle = trigger.get("replenishment_cycle") or 60
    safe_days = int(settings.SAFE_STOCK_DAYS or 0)
    trigger_line = replenishment_cycle + safe_days
    if inventory_days is None:
        inventory_days = 30  # 无有效数据时按默认30天计

    if is_new_product:
        # ── 新品：沿用原逻辑 ──
        # 断货风险（与触发线保持一致：≤触发线按超出比例线性降至30，超过触发线按超出比例降到0）
        if inventory_days <= 0:
            shortage_score = 100  # 断货（含可用库存=0）→ 满分
        elif inventory_days <= trigger_line:
            shortage_score = 30 + round(70 * (1 - inventory_days / trigger_line))
        else:
            shortage_score = max(0, round(30 * (1 - (inventory_days - trigger_line) / trigger_line)))

        # 销量趋势：把预测窗口总量按「生命周期天数」折算成等效总量，再套原分档阈值
        # 折算基准天数 = min(180, 到活动结束天数)：窗口不足 180 天时不外推（等效量=窗口总量），
        # 窗口超过 180 天时只按 180 天折算（t5 已把窗口截到活动结束时间，不外推到活动结束后）
        recent_3 = float(forecast.get("forecast_total", 0) or 0)
        _win_days = int((forecast.get("forecast_window") or {}).get("days") or 0)
        _basis_days = min(180, _win_days) if _win_days > 0 else 0
        if recent_3 > 0 and _basis_days > 0:
            recent_3 = recent_3 / _win_days * _basis_days
        actual_3 = int(inventory.get("thirty_volume", 0) or 0) * 3
        if recent_3 <= 0:
            recent_3 = actual_3
        elif actual_3 > recent_3:
            recent_3 = actual_3  # 预测明显低估时按实际销量（近30天×3）
        if recent_3 >= 1200:
            trend_score = 100
        elif recent_3 >= 600:
            trend_score = 80
        elif recent_3 >= 300:
            trend_score = 60
        elif recent_3 >= 120:
            trend_score = 40
        elif recent_3 >= 40:
            trend_score = 20
        else:
            trend_score = 10
        # 库存紧急：与断货风险共用触发线
        urgency = max(0, min(100, round((1 - inventory_days / trigger_line) * 100)))
        # 权重：SCORE_BALANCE_*（默认1.0均分）
        balances = [
            settings.SCORE_BALANCE_SHORTAGE,
            settings.SCORE_BALANCE_TREND,
            settings.SCORE_BALANCE_PROFIT,
            settings.SCORE_BALANCE_LIFE,
            settings.SCORE_BALANCE_URGENCY,
            settings.SCORE_BALANCE_TRANSPORT,
        ]
    else:
        # ── 老品：按《老品采购评分指标》分档 ──
        # ① 断货风险(20%)：库存售卖天数 ÷ 补货周期 分档
        ratio = (inventory_days / replenishment_cycle) if replenishment_cycle > 0 else 1.0
        if ratio <= 0.9:
            shortage_score = 100
        elif ratio <= 1.0:
            shortage_score = 90
        elif ratio <= 1.2:
            shortage_score = 60
        elif ratio <= 1.4:
            shortage_score = 40
        else:
            shortage_score = 0

        # ② 销量趋势(25%)：用近30天同比 G = (今年近30天-去年同30天)/去年同30天 分档；
        #    无去年同期数据时，把去年同30天视作=今年近30天 → G=0 → 落 [−1%,1%] 档 → 95 分；
        #    近30天本身也无销量时（this_30d 无效/≤0）同样按 95 计（去年同30=今年近30=0）。
        this_30d = inventory.get("thirty_volume")
        last_30d = inventory.get("last_year_thirty_volume")
        if this_30d is not None and last_30d and last_30d > 0:
            G = (float(this_30d) - float(last_30d)) / float(last_30d)
        else:
            G = 0.0  # 无去年同期 → 视作同比持平（去年同30=今年近30），避免一律60/走总量分档
        if G > 0.01:
            trend_score = 100    # 增长>1%
        elif G >= -0.01:
            trend_score = 95     # 稳定，单量持平，1%以内
        elif G >= -0.05:
            trend_score = 90     # 下降1%~5%
        elif G >= -0.10:
            trend_score = 80     # 下降5%~10%
        elif G >= -0.20:
            trend_score = 70     # 下降10%~20%
        else:
            trend_score = 60     # 下降>20%

        # ⑤ 库存紧急(10%)：<15天→100；15-30天→100；30-90天→80；>90天→0
        danger = int(settings.INVENTORY_DANGER_MAX_DAYS or 15)
        low = int(settings.INVENTORY_LOW_MAX_DAYS or 30)
        healthy = int(settings.INVENTORY_HEALTHY_MAX_DAYS or 90)
        if inventory_days < danger:
            urgency = 100    # 危险库存<15天
        elif inventory_days < low:
            urgency = 100    # 偏低库存15-30天
        elif inventory_days <= healthy:
            urgency = 80     # 健康库存30-90天
        else:
            urgency = 0      # 过量库存>90天

        # 权重：①20% / ②25% / ③25% / ④10% / ⑤10% / ⑥10%
        balances = [20.0, 25.0, 25.0, 10.0, 10.0, 10.0]

    # 利润空间（新品20%/老品25%）：领星 profit_rate 缺失时用修正后成本表海运利润率兜底
    profit_rate = _safe_float(product.profit_rate, None)
    if profit_rate is None:
        try:
            _ct = new_product_policy.calc_cost_table(product)
            profit_rate = (_ct.get("channels") or {}).get("sea", {}).get("margin") or 0.0
        except Exception:
            profit_rate = 0.0
    if is_new_product:
        # 新品「利润空间」唯一分数：DeepSeek evaluate_new_product_profit_space 综合评分
        # （C1广告/C2Listing/C3毛利/C4成本表/C5剩余天数）；不做线性公式、不做失败回退，None → 0
        profit_score = int(new_product_profit_score or 0)
    else:
        # ③ 利润空间(25%)：单件毛利率分档
        if profit_rate >= 0.10:
            profit_score = 100    # ≥10%
        elif profit_rate >= 0.05:
            profit_score = 90     # 5%~10%
        elif profit_rate >= 0.01:
            profit_score = 80     # 1%~5%
        elif profit_rate >= -0.05:
            profit_score = 60     # 负5%~0%
        elif profit_rate >= -0.10:
            profit_score = 50     # 负5%~负10%
        else:
            profit_score = 0      # 低于负10%

    # 生命周期（10%）
    if is_new_product:
        life_cycle_scores = {
            "启动期": 70, "增长期": 90, "热卖期": 100,
            "成熟期": 60, "下降期": 20,
            "新品期": 90, "成长期": 80, "衰退期": 20, "未知": 50,
        }
    else:
        # ④ 生命周期(10%)：启动90/增长100/热卖100/成熟70/下降30
        life_cycle_scores = {
            "热卖期": 100, "增长期": 100, "成熟期": 70, "启动期": 90, "下降期": 30,
            "新品期": 90, "成长期": 80, "衰退期": 20, "未知": 50,
        }
    life_score = life_cycle_scores.get(life_cycle, 50)

    # 运输可达（10%）- 仅在有利润的渠道中判断能否赶上销售窗口
    #   ① 先取成本表盈利渠道（sea/air/express 中 Profit>0 者）；三渠道全亏 → 无可选渠道 → 0 分；
    #   ② 有利润渠道全部赶不上销售窗口 → 0 分；否则按能赶上的最优（最快）有利润渠道给分。
    #   成本表异常时不阻塞评分，退化为不限制渠道。
    try:
        _ct = new_product_policy.calc_cost_table(product)
        profitable_modes = set(_ct.get("profitable_modes") or [])
    except Exception:  # noqa: BLE001
        profitable_modes = {"sea", "air", "express"}

    if stage_end:
        tc = trigger.get("transport_cycles") or {}
        sea_days = int(tc.get("sea") or 0)
        air_days = int(tc.get("air") or 0)
        express_days = int(tc.get("express") or 0)
        if not sea_days:
            lt = int(trigger.get("lead_time") or 0) or 30
            is_peak = 8 <= date.today().month <= 12
            sea_days = lt + (settings.SEA_PEAK_DAYS if is_peak else settings.SEA_SLOW_DAYS)
            air_days = lt + (settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS)
            express_days = lt + (settings.EXPRESS_PEAK_DAYS if is_peak else settings.EXPRESS_SLOW_DAYS)
        today = date.today()
        if "sea" in profitable_modes and today + timedelta(days=sea_days) <= stage_end:
            transport_score = 100  # 有利润的海运可按时到仓
        elif "air" in profitable_modes and today + timedelta(days=air_days) <= stage_end:
            transport_score = 80   # 有利润的空派可按时到仓
        elif "express" in profitable_modes and today + timedelta(days=express_days) <= stage_end:
            transport_score = 60   # 有利润的快递能赶上
        else:
            transport_score = 0    # 有利润渠道全部赶不上销售窗口（或三渠道全亏）
    elif purchase_window and purchase_window.get("can_purchase", True):
        # 有利润的推荐渠道才给分；推荐渠道亏损/三渠道全亏 → 0
        _tmap = {"海运": ("sea", 100), "空派": ("air", 80), "快递": ("express", 60)}
        _mode, _score = _tmap.get(purchase_window.get("recommended_transport", "海运"), (None, 0))
        transport_score = _score if (_mode and _mode in profitable_modes) else 0
    elif purchase_window:
        transport_score = 0    # 无法赶上销售窗口（can_purchase=False）
    else:
        transport_score = 70 if profitable_modes else 0  # 默认：需有盈利渠道

    # 总分（评分平衡系数：各维度 × 系数后按系数总和归一化，保持 0-100）
    # 新品用 SCORE_BALANCE_*（.env，默认1.0均分）；老品用固定 ①20/②25/③25/④10/⑤10/⑥10
    bal_total = sum(max(b, 0.0) for b in balances) or 1.0
    norm_w = [max(b, 0.0) / bal_total for b in balances]
    total_score = min(max(round(
        shortage_score * norm_w[0]
        + trend_score * norm_w[1]
        + profit_score * norm_w[2]
        + life_score * norm_w[3]
        + urgency * norm_w[4]
        + transport_score * norm_w[5]
    ), 0), 100)

    # 采购级别
    if total_score >= 80:
        level = "立即采购"
    elif total_score >= 60:
        level = "观察"
    else:
        level = "暂停"

    # 零销量/零预测产品不判"暂停/观察"：归"未触发"，避免低销量产品混入暂停清单
    zero_sales = (
        (inventory.get("thirty_volume") or 0) <= 0
        and (inventory.get("recent_3_days_avg") or 0) <= 0
        and (forecast.get("forecast_total") or 0) <= 0
    )
    if zero_sales and level in ("观察", "暂停"):
        level = "未触发"

    inventory["urgency_score"] = urgency

    return {
        "purchase_score": total_score,
        "purchase_level": level,
        "score_detail": {
            "shortage_score": {"value": shortage_score, "weight": round(norm_w[0], 3), "balance": max(balances[0], 0.0), "label": "断货风险"},
            "trend_score": {"value": trend_score, "weight": round(norm_w[1], 3), "balance": max(balances[1], 0.0), "label": "销量趋势"},
            "profit_score": {"value": profit_score, "weight": round(norm_w[2], 3), "balance": max(balances[2], 0.0), "label": "利润空间"},
            "life_score": {"value": life_score, "weight": round(norm_w[3], 3), "balance": max(balances[3], 0.0), "label": "生命周期"},
            "urgency": {"value": urgency, "weight": round(norm_w[4], 3), "balance": max(balances[4], 0.0), "label": "库存紧急"},
            "transport_score": {"value": transport_score, "weight": round(norm_w[5], 3), "balance": max(balances[5], 0.0), "label": "运输可达"},
        },
    }


