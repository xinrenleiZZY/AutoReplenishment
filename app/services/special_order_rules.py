# -*- coding: utf-8 -*-
"""特例加订/不加订规则（人工补货业务经验规则，来自《加订不加订理由》）

命中「加订」规则 → 直接加到一定分数（无视公式低分，走风险加订）；
命中「不加订」规则 → 阻止升级（即使公式评分高也不加订）。
结果以「特例」标签写入 score_detail，供前端溯源展示。

数据可得性说明：
- 情况3（对手高峰期溢价）依赖对手价格数据，系统暂无 → 不自动判定，仅输出提示
- 情况4 的自然排名 P1-P2 无数据 → 只按 ACOS + 同比判定，命中后注明"排名未校验"
"""

import logging
from datetime import date, timedelta

logger = logging.getLogger(__name__)

ADD_SCORE = 85      # 特例加订直接加到该分数（≥立即采购线）
BLOCK_SCORE = 55    # 特例不加订封顶该分数（< 观察线，阻止升级）


def _rating_ok(stars) -> bool:
    """链接评分：无评分 或 0 分 或 ≥4 分 视为达标"""
    if stars is None:
        return True
    try:
        s = float(stars)
    except (ValueError, TypeError):
        return True
    return s <= 0 or s >= 4.0


def _daily_avg(daily_orders, days: int) -> float:
    recent = daily_orders[-days:] if daily_orders else []
    return sum(recent) / max(len(recent), 1)


def _days_on_sale(product, current_date) -> int | None:
    if not product.list_date:
        return None
    return (current_date - product.list_date).days


def evaluate_special_orders(
    product,
    inventory: dict,
    acos: float | None,
    daily_orders: list,
    history: dict,
    lead_time: int | None,
    current_date: date = None,
    festival_end: date = None,
    buffer_days: int = 0,
) -> dict:
    """评估特例加订/不加订规则，返回命中情况与标签

    Args:
        festival_end: 节日结束时间（festival_calendar.festival_end），用于计算产品剩余可售卖时间
        buffer_days: 缓存天数（装饰品14/非装饰品3，由语义分类判定）

    返回:
      {
        "add_cases": [{"id", "name", "detail"}...],
        "block_cases": [...],
        "add_label": str | None,   # 特例加订标签（含情况编号）
        "block_label": str | None, # 特例不加订标签
      }
    """
    current_date = current_date or date.today()
    result = {"add_cases": [], "block_cases": [], "add_label": None, "block_label": None}

    is_new = (product.product_stage or "").strip() in ("新品", "new")
    acos = float(acos) if acos is not None else None
    stars = getattr(product, "stars", None)
    days_on_sale = _days_on_sale(product, current_date)

    avg7 = _daily_avg(daily_orders, 7)
    recent7 = daily_orders[-7:] if daily_orders else []
    sum7 = sum(recent7)
    all7_order = len(recent7) == 7 and all(x > 0 for x in recent7)

    monthly_avg = history.get("monthly_avg") or 0
    last_year_same_month = history.get("last_year_same_month") or 0
    # 增长倍数口径（情况4）：节日老品用「节日窗口趋势系数」（今年近30天÷去年同30天，Step 4 新口径）；
    # 无节日老品（长期产品/未匹配 festival_calendar）沿用旧口径（今年均量÷去年同月）
    # 注：长期产品的 Step 4/5 已改窗口口径（lt_trend_coeff），此处情况4 本轮不改，仍走旧口径
    trend_coeff = history.get("trend_coeff")
    if trend_coeff is not None:
        yoy = float(trend_coeff)
        yoy_raw = history.get("trend_coeff_raw")
        yoy_raw = float(yoy_raw) if yoy_raw is not None else yoy
        # 最终趋势系数上限 1.5：被封顶时文字表述标注原值
        if yoy_raw - yoy > 1e-9:
            yoy_desc = f"节日窗口趋势系数{yoy:.2f}（原值：{yoy_raw:.2f}）（今年近30天÷去年同30天）"
        else:
            yoy_desc = f"节日窗口趋势系数{yoy:.2f}（今年近30天÷去年同30天）"
    else:
        yoy = (monthly_avg / last_year_same_month) if last_year_same_month > 0 else None
        yoy_desc = f"今年均量{monthly_avg}为去年同月{last_year_same_month}的{yoy:.0%}" if yoy is not None else ""

    inventory_days = inventory.get("inventory_days")
    replenishment_cycle = inventory.get("replenishment_cycle")

    # 产品剩余可售卖时间 = 节日结束时间 − 当前时间 − 缓存天数（缓冲天数）
    remaining_sellable_days = None
    if festival_end is not None:
        remaining_sellable_days = (festival_end - current_date).days - (buffer_days or 0)

    # 成本表盈利性：全渠道亏损时老品/连续出单加订一律不命中（本质要赚钱）
    cost_table = None
    all_loss = False
    cost_profits = {}
    try:
        from app.services import new_product_policy

        cost_table = new_product_policy.calc_cost_table(product)
        cost_profits = {m: c["profit"] for m, c in (cost_table.get("channels") or {}).items()}
        all_loss = not bool(cost_table.get("all_profitable"))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[{getattr(product, 'asin', '')}] 成本表计算失败，跳过盈利门槛: {e}")

    # ══════════════ 加订规则 ══════════════
    # 情况1：新品上架≤14天，ACOS≤30%且趋势变好，日均单量≥5，评分无/0/≥4（利润负也可加订）
    if is_new and days_on_sale is not None and days_on_sale <= 14 \
            and acos is not None and acos <= 0.30 and avg7 >= 5 and _rating_ok(stars):
        result["add_cases"].append({
            "id": 1, "name": "新品快测加订(≤14天)",
            "detail": f"新品上架{days_on_sale}天，ACOS={acos:.1%}≤30%，日均单量{avg7:.1f}≥5，评分达标，利润负也可加订300套",
        })

    # 情况2：新品上架≤7天，ACOS≤20%，日均单量≥3，评分无/0/≥4（利润负也可加订）
    if is_new and days_on_sale is not None and days_on_sale <= 7 \
            and acos is not None and acos <= 0.20 and avg7 >= 3 and _rating_ok(stars):
        result["add_cases"].append({
            "id": 2, "name": "新品快测加订(≤7天)",
            "detail": f"新品上架{days_on_sale}天，ACOS={acos:.1%}≤20%，日均单量{avg7:.1f}≥3，评分达标，利润负也可加订300套",
        })

    # 情况3：成本表利润1-2美金 + 对手高峰溢价≥5美金 —— 对手价格数据缺失，不自动判定
    # 情况4：老品，同比增长≥10%（节日老品按节日窗口趋势系数，Q9 新口径） + 近30天ACOS≤35%
    if not is_new and not all_loss and yoy is not None and yoy >= 1.10 \
            and acos is not None and acos <= 0.35:
        result["add_cases"].append({
            "id": 4, "name": "老品同比增长加订",
            "detail": f"{yoy_desc}（≥10%），ACOS={acos:.1%}≤35%；自然排位P1-P2未校验（系统暂无排名数据）",
        })

    # 情况5：仅新品，连续7天每天出单 + 一周出单≥10 + ACOS≤30%（加分：ACOS≤20%）
    #   说明：系统销售数据不区分广告/自然单，以「一周总出单 sum7」近似「广告一周出单≥10」
    if is_new and not all_loss and all7_order and sum7 >= 10 and acos is not None and acos <= 0.30:
        bonus = "，ACOS≤20%加分" if acos <= 0.20 else ""
        result["add_cases"].append({
            "id": 5, "name": "新品连续出单加订",
            "detail": f"新品连续7天每天出单（周共{sum7}单≥10），ACOS={acos:.1%}≤30%{bonus}",
        })

    # ══════════════ 不加订规则 ══════════════
    # 前提：利润率≥15% + ACOS≤30% + 评分无/0/≥4
    profit_rate = getattr(product, "profit_rate", None)
    try:
        profit_rate = float(profit_rate) if profit_rate is not None else None
    except (ValueError, TypeError):
        profit_rate = None

    # 情况3（成本表无盈利渠道 → 亏损不加订）：不依赖利润率字段/ACOS/评分前提；
    # 但新品快测加订（情况1/2）允许负利润测试，不冲突
    if all_loss and not any(c["id"] in (1, 2) for c in result["add_cases"]):
        result["block_cases"].append({
            "id": 3, "name": "成本上涨无利润不加订",
            "detail": f"成本表三渠道 Profit 均为负（{cost_profits}），亏损不加订",
        })

    if profit_rate is not None and profit_rate >= 0.15 \
            and acos is not None and acos <= 0.30 and _rating_ok(stars):
        # 情况1：产品剩余可售卖时间 < 补货周期 → 放弃
        if remaining_sellable_days is not None and replenishment_cycle is not None \
                and remaining_sellable_days < replenishment_cycle:
            result["block_cases"].append({
                "id": 1, "name": "剩余时间不足不加订",
                "detail": f"利润率{profit_rate:.0%}≥15%、ACOS={acos:.1%}≤30%、评分达标，但产品剩余可售卖时间{remaining_sellable_days}天 < 补货周期{replenishment_cycle}天（体积大仅海运可盈利），放弃加订",
            })
        # 情况2：大货工期 > 剩余可售时间 → 放弃
        if inventory_days is not None and lead_time is not None and lead_time > inventory_days:
            result["block_cases"].append({
                "id": 2, "name": "工期超剩余时间不加订",
                "detail": f"利润率{profit_rate:.0%}≥15%、ACOS={acos:.1%}≤30%、评分达标，但大货工期{lead_time}天 > 剩余可售{inventory_days}天，放弃加订",
            })
    # 标签
    if result["add_cases"]:
        ids = "、".join(f"情况{c['id']}" for c in result["add_cases"])
        result["add_label"] = f"特例加订[{ids}]"
    if result["block_cases"]:
        ids = "、".join(f"情况{c['id']}" for c in result["block_cases"])
        result["block_label"] = f"特例不加订[{ids}]"

    logger.info("特例判定 asin=%s add=%s block=%s",
                getattr(product, "asin", "?"), result["add_label"], result["block_label"])
    return result
