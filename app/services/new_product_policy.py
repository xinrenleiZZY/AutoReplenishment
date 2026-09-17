# -*- coding: utf-8 -*-
"""
新品补货策略模块（需求文档第六章 v2 2026-07-31 版本）

新品只使用本模块规则，老品模块（预测/批次/评分）新品不参与。

流程:
  1. 触发条件: 连续3天或以上日均单量 > 5 单 → 才触发补货计算
  2. 硬性指标: 广告ACOS < 55%（SIF获取），不达标终止
  3. 参考指标: Listing评分/评论（SIF）、产品毛利润/毛利率（领星利润报表）
  4. P0 成本表: Profit为正才可加订（运费/售价影响）
  5. P1 剩余售卖天数: > 14天 才加订，≤ 14天 终止

最高准则:
  - 成本表 Profit 为负数 → 直接终止加订
  - 剩余售卖天数 < 14天 → 直接终止加订

=== 节日/主题产品补货 ===
  需求截止日期 = 节日日期 - 产品销售类型缓冲天数（装饰14/非装饰3）
  补货周期 = 大货生产周期 + 运输时间
  预测日销 = 基准日销 × 生命周期日销系数 × 安全系数（安全系数用于预测销量）
             启动期：基准日销 × 启动期系数^(经过天数÷10) × 安全系数
             （经过天数 = 预测日 − 节日「启动期」起始日，由调用方传入 launch_start）
  库存售卖天数 = 可使用库存 ÷ 预测日销
  情况一(预计售罄<补货到货): 空运补缺口 + 海运补货
  情况二(预计售罄≥补货到货): 补货数量 = 销售窗口内每日预测(已含安全系数)求和

=== 长期产品补货 ===
  预测日销 = 基准销量 × 安全系数（不乘生命周期系数）
  库存售卖天数 = 可使用库存 ÷ 预测日销
  补货数量 = 预测日销 × 补货周期
  缺货缺口 = (补货到货-预计售罄) × 预测日销 → 空运/快递补缺口

=== 分流规则 ===
  product_type=长期产品 → 长期分支；否则有节日日期 → 节日分支；否则 → 长期分支。
  （长期产品即使误填 festival 也不走节日分支）
"""

import json
import logging
import math
from datetime import date, datetime, timedelta
from typing import Optional

from app.config import settings
from app.services.lifecycle import (
    LIFECYCLE_LAUNCH, LIFECYCLE_GROWTH, LIFECYCLE_HOT,
    LIFECYCLE_MATURE, LIFECYCLE_DECLINE, LIFECYCLE_CLEARANCE,
)

logger = logging.getLogger(__name__)

# ==================== 常量 ====================
# 触发条件
TRIGGER_MIN_DAYS = 3          # 连续3天
TRIGGER_MIN_DAILY_ORDER = 5   # 日均单量 > 5

# ACOS 硬性指标（2026-07-31 版本改为 55%）
ACOS_MAX = 0.55               # ACOS < 55%

# 剩余售卖天数门槛
MIN_SELLING_DAYS = 14         # > 14天才可加订

# 节日产品缓冲天数
DECORATION_BUFFER_DAYS = 14   # 装饰类产品: 节日前14天结束售卖
NON_DECORATION_BUFFER_DAYS = 3  # 非装饰/DIY: 节日前3天结束售卖

# 物流淡旺季月份
PEAK_MONTHS = set(range(8, 13))   # 旺季 8-12月
SLOW_MONTHS = set(range(1, 8))    # 淡季 1-7月

# 运输方式配置: 名称 → (淡季天数, 旺季天数, 淡季运费(人民币/件), 旺季运费(人民币/件))
# 业务规则：淡季=便宜、旺季=贵
TRANSPORT_MODES = {
    "sea":    {"label": "海运", "slow_days": 30, "peak_days": 45,
               "slow_fee": 15, "peak_fee": 17},
    "air":    {"label": "空派", "slow_days": 10, "peak_days": 15,
               "slow_fee": 60, "peak_fee": 70},
    "express": {"label": "快递", "slow_days": 3, "peak_days": 6,
               "slow_fee": 70, "peak_fee": 80},
}

# 生命周期 → (日均销量系数, 库存安全系数)
LIFECYCLE_FACTORS = {
    LIFECYCLE_LAUNCH: (1.03, 1.0),   # 启动期: 每10天增长3%
    LIFECYCLE_GROWTH: (1.15, 1.15),  # 增长期
    LIFECYCLE_HOT: (1.30, 1.30),     # 热卖期
    LIFECYCLE_MATURE: (1.00, 1.20),  # 成熟期
    LIFECYCLE_DECLINE: (0.70, 1.0),  # 下降期
    LIFECYCLE_CLEARANCE: (0.50, 1.0),  # 清库存期（保守）
}

# 长期产品默认补货周期（按工期）
LONG_TERM_DEFAULT_CYCLE = {
    (1, 10): 45,    # 工期7-10天 → 45天
    (11, 20): 50,   # 工期11-20天 → 50天
    (21, 999): 60,  # 工期21-30天+ → 60天
}

# ==================== 可配置参数 ====================
def _parse_extra_fees(raw) -> dict:
    """解析成本表单件附加费用（美元/件）

    接受 dict 或 JSON 字符串；键为费用名（如 packing_card/inbound/storage/ad/
    return_loss/misc/over_threshold_loss），值为美元/件。无效输入返回空 dict。
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(str(raw))
        except (ValueError, TypeError):
            logger.warning("成本表附加费用 COST_EXTRA_FEES_USD 解析失败: %r", raw)
            return {}
        if not isinstance(data, dict):
            return {}
    result = {}
    for k, v in data.items():
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        if val > 0:
            result[str(k)] = round(val, 6)
    return result


# 由 config_service 覆盖（apply_config），未配置时使用默认值
_CFG = {
    "trigger_days": TRIGGER_MIN_DAYS,
    "trigger_min_order": TRIGGER_MIN_DAILY_ORDER,
    "acos_max": ACOS_MAX,
    "min_selling_days": MIN_SELLING_DAYS,
    "decoration_buffer_days": DECORATION_BUFFER_DAYS,
    "non_decoration_buffer_days": NON_DECORATION_BUFFER_DAYS,
    "transport_modes": TRANSPORT_MODES,
    "lifecycle_factors": LIFECYCLE_FACTORS,
    "long_term_default_cycle": LONG_TERM_DEFAULT_CYCLE,
    "long_term_safety_factor": 1.2,
    "usd_cny_rate": 7.2,
    # 成本表（三渠道 Profit）模板参数：圣诞毛衣奖牌成本表 2026.7.30 口径
    "cost_exchange_rate": settings.COST_EXCHANGE_RATE,
    "cost_referral_ratio": settings.COST_REFERRAL_RATIO,
    "cost_referral_min": settings.COST_REFERRAL_MIN,
    "cost_packing_ratio": settings.COST_PACKING_RATIO,
    "cost_misc_ratio": settings.COST_MISC_RATIO,
    "cost_ad_ratio": settings.COST_AD_RATIO,
    "cost_return_ratio": settings.COST_RETURN_RATIO,
    "cost_inbound_fee": settings.COST_INBOUND_FEE,
    "cost_storage_fee": settings.COST_STORAGE_FEE,
    "cost_return_threshold": settings.COST_RETURN_THRESHOLD,
    "cost_over_threshold_fee": settings.COST_OVER_THRESHOLD_FEE,
    "cost_default_weight_kg": settings.COST_DEFAULT_WEIGHT_KG,
    "extra_fees_usd": _parse_extra_fees(settings.COST_EXTRA_FEES_USD),  # 保留兼容（新模板公式不再读取）
}


def apply_config(cfg: dict):
    """用配置服务参数覆盖模块默认值（只更新传入的键）"""
    if not cfg:
        return
    for key, value in cfg.items():
        if key in _CFG and value is not None:
            _CFG[key] = value
    if "extra_fees_usd" in cfg:
        _CFG["extra_fees_usd"] = _parse_extra_fees(cfg["extra_fees_usd"])
    # 同步到模块级常量（旧函数仍可用）
    if "trigger_days" in cfg:
        global TRIGGER_MIN_DAYS
        TRIGGER_MIN_DAYS = int(_CFG["trigger_days"])
    if "trigger_min_order" in cfg:
        global TRIGGER_MIN_DAILY_ORDER
        TRIGGER_MIN_DAILY_ORDER = float(_CFG["trigger_min_order"])
    if "acos_max" in cfg:
        global ACOS_MAX
        ACOS_MAX = float(_CFG["acos_max"])
    if "min_selling_days" in cfg:
        global MIN_SELLING_DAYS
        MIN_SELLING_DAYS = int(_CFG["min_selling_days"])
    if "decoration_buffer_days" in cfg:
        global DECORATION_BUFFER_DAYS
        DECORATION_BUFFER_DAYS = int(_CFG["decoration_buffer_days"])
    if "non_decoration_buffer_days" in cfg:
        global NON_DECORATION_BUFFER_DAYS
        NON_DECORATION_BUFFER_DAYS = int(_CFG["non_decoration_buffer_days"])
    if "transport_modes" in cfg:
        global TRANSPORT_MODES
        TRANSPORT_MODES = _CFG["transport_modes"]
    if "lifecycle_factors" in cfg:
        global LIFECYCLE_FACTORS
        LIFECYCLE_FACTORS = _CFG["lifecycle_factors"]


def _float(v) -> float:
    """安全转 float"""
    if v is None or v == "":
        return 0.0
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def unit_cost(product) -> float:
    """采购单价（美元/件）

    cost_price 来自领星 ERP 产品管理的 cg_price（采购报价，人民币元/件），
    按 USD_CNY_RATE 折算成美元参与利润计算。
    （2026-08-07 已用领星利润报表真实毛利率交叉验证：cg_price 为人民币单件价，
     直接按汇率折算与真实毛利吻合；按箱规折算/按美元使用均显著偏离。）
    """
    cost = _float(getattr(product, "cost_price", None))
    if cost <= 0:
        return 0.0
    usd_cny_rate = float(_CFG.get("usd_cny_rate", 7.2) or 7.2)
    return round(cost / usd_cny_rate, 4)


def _safe_int(v, default=0) -> int:
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return default


# ==================== 成本表（P0） ====================

def calc_cost_table(product, cfg: dict = None, price_override: float | None = None,
                    override: dict | None = None) -> dict:
    """按成本表模板（圣诞毛衣奖牌成本表 2026.7.30）计算 3 个运费渠道的利润（P0）

    Profit = 售价 − MC − P卡费 − 分拣费(FBA费) − 佣金 − 入库配置费
             − 仓储费 − 广告费预算 − 退货成本亏损平摊 − 超阈值亏损 − 附加费
    MC = 采购总成本(¥) / 汇率 + 运费(USD)
    运费：有重量（weight_kg/尺寸）时按 计费重量kg × 运费单价(¥/kg) / 汇率；
          无重量时按件计费（运费单价 ¥/件 / 汇率）兜底。
    各费用默认按模板口径（可配置）：
      佣金 = MAX(1, 售价×15%)；P卡费=(售价−分拣费−佣金)×3%；
      广告费=售价×17%；退货平摊=售价×3%；附加费=分拣费×3.5%；
      入库配置费 $0.32；仓储费 $0.1093（无尺寸兜底）；超阈值亏损默认0。
    price_override: 模拟售价（如竞对旺季售价），不传则用产品当前售价。
    override: product_costs 覆盖字段（优先级最高），键同 ProductCost 模型。
    """
    cfg = cfg or _CFG
    override = override or getattr(product, "_cost_override", None) or {}
    overridden = [k for k, v in override.items() if v is not None]

    # 成本表售价($)：改用当前 Listing 价（listing_price），旧值 price（与 regular_price 相同=日常价）已弃用
    price = (override.get("price") if override.get("price") is not None
             else price_override if price_override is not None
             else _float(getattr(product, "listing_price", None) or getattr(product, "price", None)))
    rate = float(override.get("exchange_rate")
                 or cfg.get("cost_exchange_rate") or cfg.get("usd_cny_rate") or 6.5)
    cost_cny = (override.get("cost_cny") if override.get("cost_cny") is not None
                else _float(getattr(product, "cost_price", None)))
    cost_usd = round(cost_cny / rate, 4) if cost_cny > 0 else 0.0
    sorting_fee = (override.get("sorting_fee") if override.get("sorting_fee") is not None
                   else _float(getattr(product, "fba_fee", None)))    # 分拣费/FBA费
    referral_fee = (override.get("referral_fee") if override.get("referral_fee") is not None
                    else _float(getattr(product, "referral_fee", None)))  # 佣金
    if referral_fee <= 0:
        referral_fee = max(float(cfg.get("cost_referral_min") or 1.0),
                           round(price * float(cfg.get("cost_referral_ratio") or 0.15), 4))
    packing_card = (override.get("packing_fee") if override.get("packing_fee") is not None
                    else round((price - sorting_fee - referral_fee) * float(cfg.get("cost_packing_ratio") or 0.03), 4))
    misc_fee = (override.get("misc_fee") if override.get("misc_fee") is not None
                else round(sorting_fee * float(cfg.get("cost_misc_ratio") or 0.035), 4))
    ad_fee = (override.get("ad_fee") if override.get("ad_fee") is not None
              else round(price * float(cfg.get("cost_ad_ratio") or 0.17), 4))
    return_loss = (override.get("return_loss") if override.get("return_loss") is not None
                   else round(price * float(cfg.get("cost_return_ratio") or 0.03), 4))
    inbound_fee = (override.get("inbound_fee") if override.get("inbound_fee") is not None
                   else float(cfg.get("cost_inbound_fee") or 0.32))
    storage_fee = (override.get("storage_fee") if override.get("storage_fee") is not None
                   else float(cfg.get("cost_storage_fee") or 0.1093))
    ret_threshold = float(cfg.get("cost_return_threshold") or 0.087)
    over_fee = float(cfg.get("cost_over_threshold_fee") or 2.08)
    over_loss = (override.get("over_threshold_loss") if override.get("over_threshold_loss") is not None
                 else round(max(0.0, (float(cfg.get("cost_return_ratio") or 0.03) - ret_threshold)) * over_fee, 4))

    # 计费重量：优先产品重量/尺寸（体积重 = 长×宽×高cm / 6000），否则用默认重量；0=按件
    weight_kg = (override.get("weight_kg") if override.get("weight_kg") is not None
                 else _float(getattr(product, "weight_kg", None)))
    if weight_kg <= 0:
        weight_kg = float(cfg.get("cost_default_weight_kg") or 0.0)
    dims = []
    for k in ("length_cm", "width_cm", "height_cm"):
        v = override.get(k)
        if v is None:
            v = getattr(product, k, None)
        dims.append(_float(v))
    if all(d > 0 for d in dims):
        volume_kg = round(dims[0] * dims[1] * dims[2] / 6000.0, 6)
        weight_kg = max(weight_kg, volume_kg)
    freight_basis = "weight" if weight_kg > 0 else "per_piece"

    peak = is_peak_season()

    channels = {}
    profitable = []
    for mode, m in (cfg["transport_modes"] or TRANSPORT_MODES).items():
        fee_cny = override.get(f"freight_{mode}_cny")
        if fee_cny is None:
            fee_cny = m["peak_fee"] if peak else m["slow_fee"]
        if freight_basis == "weight":
            fee_usd = round(weight_kg * float(fee_cny) / rate, 4)
        else:
            fee_usd = round(float(fee_cny) / rate, 4)
        mc = round(cost_usd + fee_usd, 4)
        profit = round(
            price - mc - packing_card - sorting_fee - referral_fee - inbound_fee
            - storage_fee - ad_fee - return_loss - over_loss - misc_fee, 4
        )
        ok = profit > 0
        channels[mode] = {
            "label": m["label"],
            "freight_fee": fee_cny,
            "freight_fee_usd": fee_usd,
            "mc": mc,
            "packing_card": packing_card,
            "sorting_fee": sorting_fee,
            "referral_fee": referral_fee,
            "inbound_fee": inbound_fee,
            "storage_fee": storage_fee,
            "ad_fee": ad_fee,
            "return_loss": return_loss,
            "over_threshold_loss": over_loss,
            "misc_fee": misc_fee,
            "profit": profit,
            "margin": round(profit / price, 4) if price > 0 else None,
            "profitable": ok,
        }
        if ok:
            profitable.append(mode)

    return {
        "price": price,
        "unit_cost": cost_usd,
        "cost_price_cny": cost_cny,
        "exchange_rate": rate,
        "dims_cm": [round(d, 2) if d > 0 else 0 for d in dims],
        "fba_fee": sorting_fee,
        "referral_fee": referral_fee,
        "freight_basis": freight_basis,
        "weight_kg": weight_kg,
        "extra_fees_usd": {},
        "extra_fees_total": 0,
        "overridden": overridden,
        "is_peak": peak,
        "channels": channels,
        "profitable_modes": profitable,
        "all_profitable": bool(profitable),
    }


def check_cost_table(table: dict) -> tuple:
    """检查成本表是否有盈利渠道（P0）"""
    if not table.get("all_profitable"):
        profits = {m: c["profit"] for m, c in table["channels"].items()}
        return False, f"成本表3渠道Profit均为负{profits}，终止加订（可填竞对旺季售价到Price后重算）"
    modes = table["profitable_modes"]
    labels = [table["channels"][m]["label"] for m in modes]
    max_profit = max((table["channels"][m]["profit"] for m in modes), default=0)
    tip = ""
    if max_profit < 1.0:
        tip = "；注意：最高Profit不足1美金，利润偏薄，建议谨慎加订"
    return True, f"成本表有盈利渠道: {labels}，可继续加订{tip}"


# ==================== 新品完整决策 ====================

def _forecast_basis_step(use_festival: bool, is_long_term: bool, base_daily_sales: float,
                         life_cycle: str, stage_factor: float, safety_factor: float,
                         plan: dict, launch_start, festival_date) -> dict:
    """构造「预测基准口径」溯源 step

    输出: 基准日销 / 阶段系数 / 安全系数 / 启动期经过天数 / 预测日销 / 逐窗口需求
    """
    base = round(float(base_daily_sales or 0), 2)
    if use_festival:
        elapsed = int(plan.get("elapsed_days", 0) or 0)
        daily = round(daily_forecast_by_stage(base_daily_sales, life_cycle, elapsed), 2)
        if life_cycle == LIFECYCLE_LAUNCH:
            formula = (f"预测日销 = 基准{base} × 启动期系数{stage_factor}^(经过{elapsed}天÷10)"
                       f" × 安全系数{safety_factor} = {daily}")
        else:
            formula = (f"预测日销 = 基准{base} × {life_cycle}系数{stage_factor}"
                       f" × 安全系数{safety_factor} = {daily}")
        windows = [
            {"mode": p.get("label"), "arrival": p.get("arrival"),
             "selling_days": p.get("selling_days"), "qty": p.get("qty"),
             "gap_qty": p.get("gap_qty")}
            for p in (plan.get("transport_plans") or [])
        ]
        reason = (f"节日产品：{formula}；需求截止日={plan.get('demand_deadline')}"
                  f"（节日{festival_date} − 缓冲）；逐窗口需求={windows}")
        data = {
            "branch": "节日产品",
            "base_daily_sales": base,
            "lifecycle": life_cycle,
            "stage_factor": stage_factor,
            "safety_factor": safety_factor,
            "launch_start": plan.get("launch_start"),
            "elapsed_days": elapsed,
            "forecast_daily": daily,
            "demand_deadline": plan.get("demand_deadline"),
            "window_demands": windows,
        }
    else:
        used_safety = float(plan.get("safety_factor", safety_factor))
        daily = round(float(plan.get("forecast_daily", 0) or 0), 2)
        branch = "长期产品" if is_long_term else "无节日新品（长期口径）"
        reason = (f"{branch}：预测日销 = 基准{base} × 安全系数{used_safety}"
                  f"（不乘生命周期系数） = {daily}；"
                  f"补货周期{plan.get('replenishment_cycle')}天 → 补货{plan.get('final_qty')}件")
        data = {
            "branch": branch,
            "base_daily_sales": base,
            "lifecycle": life_cycle,
            "safety_factor": used_safety,
            "forecast_daily": daily,
            "replenishment_cycle": plan.get("replenishment_cycle"),
            "base_qty": plan.get("base_qty"),
            "gap_qty": plan.get("gap_qty"),
            "gap_mode": plan.get("gap_mode"),
        }
    return {"name": "预测基准口径", "status": "pass", "reason": reason, "data": data}


def new_product_decision(
    product,
    daily_orders: list,
    acos: Optional[float],
    available_stock: int,
    base_daily_sales: float,
    life_cycle: str,
    festival_date: Optional[date],
    is_decoration: bool,
    lead_time: int,
    cfg: dict = None,
    current_date: date = None,
    launch_start: Optional[date] = None,
) -> dict:
    """新品补货完整决策（需求文档第六章，新品只走本流程）

    门禁顺序:
      1. 触发条件: 连续N天日均单量 > 阈值
      2. ACOS 硬性指标 ≤ 55%（无数据按达标放行，提示补充）
      3. P0 成本表 Profit（3渠道运费）
      4. 分流: product_type=长期产品 → 长期分支；否则有节日日期 → 节日分支；否则 → 长期分支
      5. P1 剩余售卖天数 > 14天
      6. 补货计划（按有利润渠道）

    Args:
        launch_start: 节日「启动期」起始日（启动期指数 系数^(经过天数÷10) 的起算点，
                      由调用方按 festival_calendar 启动期区间起始日传入；长期/无节日传 None）
    """
    cfg = cfg or _CFG
    current_date = current_date or date.today()
    steps = []

    # ── 1. 触发条件 ──
    triggered = check_trigger(daily_orders)
    t_days = int(cfg.get("trigger_days", 3))
    recent_orders = daily_orders[-t_days:] if daily_orders else []
    steps.append({
        "name": "新品触发条件",
        "status": "pass" if triggered else "fail",
        "reason": f"最近{t_days}天单量={recent_orders}，"
                  f"日均={sum(recent_orders) / max(len(recent_orders), 1):.1f}单，"
                  f"阈值>{cfg.get('trigger_min_order', 5)}单",
    })
    if not triggered:
        steps.append({"name": "后续门禁与补货计划", "status": "skip",
                      "reason": "因触发条件未通过而跳过（ACOS/成本表/剩余售卖天数/补货数量均未计算）"})
        return {
            "triggered": False,
            "level": "未触发",
            "suggested_qty": 0,
            "reason": "新品连续3天日均单量未超5单，暂不触发补货计算",
            "steps": steps,
        }

    # ── 2. ACOS 硬性指标 ──
    ok_acos, reason_acos = check_acos(acos)
    steps.append({"name": "ACOS硬性指标", "status": "pass" if ok_acos else "fail",
                  "reason": f"ACOS={acos if acos is None else f'{acos:.1%}'}，{reason_acos}"})
    if not ok_acos:
        steps.append({"name": "后续门禁与补货计划", "status": "skip",
                      "reason": "因ACOS硬性指标未达标而跳过（成本表/剩余售卖天数/补货数量均未计算）"})
        return {
            "triggered": True,
            "level": "终止",
            "acos": acos,
            "suggested_qty": 0,
            "reason": reason_acos,
            "steps": steps,
        }

    # ── 3. P0 成本表利润 ──
    cost_table = calc_cost_table(product, cfg)
    ok_cost, reason_cost = check_cost_table(cost_table)
    steps.append({
        "name": "成本表Profit(P0)",
        "status": "pass" if ok_cost else "fail",
        "reason": reason_cost,
        "data": {
            "price": cost_table["price"],
            "unit_cost": cost_table["unit_cost"],
            "fba_fee": cost_table["fba_fee"],
            "referral_fee": cost_table["referral_fee"],
            "channels": cost_table["channels"],
        },
    })
    if not ok_cost:
        steps.append({"name": "后续门禁与补货计划", "status": "skip",
                      "reason": "因成本表Profit(P0)未通过而跳过（剩余售卖天数/补货数量均未计算）"})
        return {
            "triggered": True,
            "level": "终止",
            "cost_table": cost_table,
            "suggested_qty": 0,
            "reason": reason_cost,
            "steps": steps,
        }

    # ── 4+5. 分流: 长期产品 / 节日产品 / 无节日的新品（一律走长期分支，不乘生命周期系数） ──
    profitable_modes = cost_table["profitable_modes"]
    stage_factor, safety_factor = LIFECYCLE_FACTORS.get(life_cycle, (1.0, 1.2))
    is_long_term = (getattr(product, "product_type", "") or "").strip() == "长期产品"
    use_festival = bool(festival_date) and not is_long_term
    plan = None
    if use_festival:
        plan = festival_replenishment_plan(
            base_daily_sales=base_daily_sales,
            lifecycle=life_cycle,
            available_stock=available_stock,
            lead_time=lead_time,
            festival_date=festival_date,
            is_decoration=is_decoration,
            current_date=current_date,
            launch_start=launch_start,
        )
        # 过滤为有利润渠道
        if plan.get("transport_plans"):
            plan["transport_plans"] = [p for p in plan["transport_plans"] if p["mode"] in profitable_modes]
            plan["recommended"] = next((p for p in plan["transport_plans"] if p.get("qty", 0) > 0), None)
            plan["can_replenish"] = plan["recommended"] is not None
    else:
        plan = long_term_replenishment_plan(
            base_daily_sales=base_daily_sales,
            available_stock=available_stock,
            lead_time=lead_time,
            safety_factor=float(cfg.get("long_term_safety_factor", 1.2)),
            current_date=current_date,
        )

    # ── 预测基准口径（溯源: 基准 × 系数 × 安全系数） ──
    steps.append(_forecast_basis_step(
        use_festival, is_long_term, base_daily_sales, life_cycle,
        stage_factor, safety_factor, plan, launch_start, festival_date,
    ))

    # ── 5. 剩余售卖天数检查（P1） ──
    remaining_days = None
    if use_festival and plan and plan.get("transport_plans"):
        # 节日产品：取有利润渠道的最大销售窗口
        remaining_days = max((p.get("selling_days", 0) for p in plan["transport_plans"]), default=None)
    ok_remaining, reason_remaining = check_remaining_days(remaining_days)
    steps.append({"name": "剩余售卖天数(P1)", "status": "pass" if ok_remaining else "fail",
                  "reason": reason_remaining,
                  "data": {"remaining_days": remaining_days}})
    if not ok_remaining:
        steps.append({"name": "建议采购数量", "status": "skip",
                      "reason": "因剩余售卖天数不足而跳过（未计算补货数量）"})
        return {
            "triggered": True,
            "level": "终止",
            "cost_table": cost_table,
            "plan": plan,
            "suggested_qty": 0,
            "reason": reason_remaining,
            "steps": steps,
        }

    # ── 6. 建议数量 ──
    suggested_qty = 0
    if plan and plan.get("recommended"):
        suggested_qty = int(plan["recommended"].get("qty", 0) or 0)
    elif plan and plan.get("final_qty") is not None:
        # 长期产品：补货数量 = 预测日销(基准×安全系数) × 补货周期
        suggested_qty = int(plan.get("final_qty", 0) or 0)
    suggested_qty = round_to_box(suggested_qty, _safe_int(getattr(product, "box_quantity", None), 1))
    if suggested_qty <= 0:
        steps.append({"name": "建议采购数量", "status": "skip",
                      "reason": "因补货数量为0而跳过（无可采购数量）"})
        return {
            "triggered": True,
            "level": "终止",
            "cost_table": cost_table,
            "plan": plan,
            "suggested_qty": 0,
            "reason": plan.get("reason", "补货数量为0"),
            "steps": steps,
        }

    reason = (plan.get("reason", "") if plan else "")
    if plan and plan.get("recommended"):
        rec = plan["recommended"]
        ch = cost_table["channels"].get(rec.get("mode", ""), {})
        reason = (f"{ch.get('label', rec.get('label', ''))}渠道到货{rec.get('arrival', '')}，"
                  f"窗口{rec.get('selling_days', 0)}天，补货{suggested_qty}件，"
                  f"成本表Profit={ch.get('profit', 0):.2f}美元/件")
    elif plan and plan.get("final_qty") is not None:
        gap = plan.get("gap_qty", 0)
        gap_mode = plan.get("gap_mode", "")
        reason = (f"{reason}；缺口{gap}件用{gap_mode}补" if gap > 0 and gap_mode else reason)
    steps.append({"name": "建议采购数量", "status": "pass",
                  "reason": reason, "data": {"suggested_qty": suggested_qty}})

    return {
        "triggered": True,
        "level": "建议采购",
        "acos": acos,
        "cost_table": cost_table,
        "plan": plan,
        "remaining_days": remaining_days,
        "suggested_qty": suggested_qty,
        "reason": reason,
        "branch": "节日产品" if use_festival else ("长期产品" if is_long_term else "无节日新品（长期口径）"),
        "steps": steps,
    }


def is_peak_season(today: date = None) -> bool:
    """判断当前是否物流旺季（8-12月）"""
    today = today or date.today()
    return today.month in PEAK_MONTHS


def get_transport_days(mode: str, today: date = None) -> int:
    """获取运输天数（按淡旺季）"""
    cfg = TRANSPORT_MODES.get(mode, TRANSPORT_MODES["sea"])
    return cfg["peak_days"] if is_peak_season(today) else cfg["slow_days"]


def get_transport_fee(mode: str, today: date = None) -> int:
    """获取运输运费（按淡旺季，美元）"""
    cfg = TRANSPORT_MODES.get(mode, TRANSPORT_MODES["sea"])
    return cfg["peak_fee"] if is_peak_season(today) else cfg["slow_fee"]


def check_trigger(daily_orders: list) -> bool:
    """
    检查是否满足补货触发条件

    连续3天或以上，日均单量超5单。

    Args:
        daily_orders: 最近N天每日订单量列表（最新在最后）

    Returns:
        bool: 是否触发补货计算
    """
    if len(daily_orders) < TRIGGER_MIN_DAYS:
        return False

    recent = daily_orders[-TRIGGER_MIN_DAYS:]
    avg = sum(recent) / len(recent)
    triggered = avg > TRIGGER_MIN_DAILY_ORDER
    logger.info(f"新品触发检查: 最近{TRIGGER_MIN_DAYS}天单量={recent}, "
                f"日均={avg:.1f}, 阈值>{TRIGGER_MIN_DAILY_ORDER}, "
                f"{'触发' if triggered else '未触发'}")
    return triggered


def check_acos(acos: Optional[float]) -> tuple:
    """
    检查广告ACOS是否达标（硬性指标，阈值55%）

    无ACOS数据时按达标放行（不终止），提示补充数据验证，避免有销量的新品被误杀。

    Args:
        acos: 最近30天整体ACOS（小数，如0.45）

    Returns:
        (达标bool, 原因str)
    """
    if acos is None:
        return True, "无ACOS数据，按达标放行（建议补充广告数据验证）"
    if acos < ACOS_MAX:
        return True, f"ACOS={acos:.1%} < 55%，达标"
    return False, f"ACOS={acos:.1%} ≥ 55%，不达标，终止加订"


def check_profit(profit: Optional[float]) -> tuple:
    """
    检查成本表 Profit 是否盈利（P0）

    最高准则: Profit为负 → 直接终止加订

    Args:
        profit: 单件利润（美元）

    Returns:
        (可加订bool, 原因str)
    """
    if profit is None:
        return True, "无成本表数据，跳过Profit检查"
    if profit < 0:
        return False, f"成本表Profit为负({profit:.2f})，终止加订"
    return True, f"成本表Profit={profit:.2f}，有盈利，可加订"


def check_remaining_days(remaining_days: Optional[int]) -> tuple:
    """
    检查剩余售卖天数（P1）

    最高准则: 剩余售卖天数 < 14天 → 直接终止加订

    Args:
        remaining_days: 剩余可售卖天数

    Returns:
        (可加订bool, 原因str)
    """
    if remaining_days is None:
        return True, "无剩余售卖天数数据，跳过检查"
    if remaining_days < MIN_SELLING_DAYS:
        return False, f"剩余售卖天数={remaining_days}天 < {MIN_SELLING_DAYS}天，终止加订"
    return True, f"剩余售卖天数={remaining_days}天 > {MIN_SELLING_DAYS}天，可加订"


# ==================== 节日/主题产品 ====================

def calc_demand_deadline(festival_date: date, is_decoration: bool) -> date:
    """
    计算需求截止日期

    需求截止日期 = 节日日期 - 产品销售类型缓冲天数
    装饰类: 14天 / 非装饰(DIY): 3天

    Args:
        festival_date: 节日日期
        is_decoration: 是否装饰类产品

    Returns:
        需求截止日期
    """
    buffer_days = DECORATION_BUFFER_DAYS if is_decoration else NON_DECORATION_BUFFER_DAYS
    deadline = festival_date - timedelta(days=buffer_days)
    logger.info(f"需求截止日: 节日={festival_date}, 类型={'装饰' if is_decoration else '非装饰'}, "
                f"缓冲={buffer_days}天, 截止={deadline}")
    return deadline


def calc_arrival_date(current_date: date, lead_time: int, mode: str) -> date:
    """计算补货到货日期 = 当前日期 + 补货周期(工期+运输)"""
    transport_days = get_transport_days(mode, current_date)
    arrival = current_date + timedelta(days=lead_time + transport_days)
    logger.info(f"到货日计算: 当前={current_date}, 工期={lead_time}, 运输={transport_days}天({mode}), 到货={arrival}")
    return arrival


def calc_inventory_sellout_days(available_stock: int, base_daily_sales: float,
                                lifecycle: str, elapsed_days: int = 0) -> float:
    """
    计算库存售卖天数

    库存售卖天数 = 可使用库存 ÷ 预测日销
    预测日销 = 基准销量 × 生命周期日销系数 × 安全系数（安全系数用于预测销量）

    Args:
        available_stock: 可使用库存(FBA可售+预留+在途)
        base_daily_sales: 基准销量（最近3天平均）
        lifecycle: 生命周期阶段
        elapsed_days: 启动期经过天数（预测日 − 节日启动期起始日），仅启动期参与指数

    Returns:
        库存售卖天数
    """
    factor, safety = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))
    daily = daily_forecast_by_stage(base_daily_sales, lifecycle, elapsed_days)
    if daily <= 0:
        return 999
    days = available_stock / daily
    logger.info(f"库存售卖测算: 库存={available_stock}, 基准={base_daily_sales}, "
                f"阶段系数={factor}, 安全系数={safety}, 经过天数={elapsed_days}, "
                f"预测日均={daily:.1f}, 可卖={days:.1f}天")
    return days


def daily_forecast_by_stage(base_daily_sales: float, lifecycle: str,
                            elapsed_days: int = 0) -> float:
    """
    按生命周期阶段计算某天的预测销量

    预测销量 = 基准销量 × 日销系数 × 安全系数（安全系数用于预测销量，
    下游窗口需求/缺口/库存售卖统一继承，补货量不再重复乘安全系数）

    启动期不是恒定系数，而是随「经过天数」增长：系数^(经过天数÷10)（默认 1.03 每10天+3%）。
    经过天数 = 预测日 − 节日「启动期」起始日（由调用方按 festival_calendar 启动期区间起始日算）。

    Args:
        base_daily_sales: 基准销量（最近3天平均单量）
        lifecycle: 生命周期阶段
        elapsed_days: 启动期经过天数（其它阶段忽略）

    Returns:
        预测日均销量（含安全缓冲）
    """
    factor, safety = LIFECYCLE_FACTORS.get(lifecycle, (1.0, 1.2))

    if lifecycle == LIFECYCLE_LAUNCH:
        # 启动期: 以启动期系数为基数，每10天增长（默认1.03/10天）
        return base_daily_sales * (factor ** (max(elapsed_days, 0) / 10)) * safety
    return base_daily_sales * factor * safety


def forecast_window_demand(base_daily_sales: float, lifecycle: str,
                           start: date, end: date,
                           launch_start: date = None) -> int:
    """
    计算销售窗口内的总预测需求

    补货数量 = ∑(窗口内每日预测销量)，安全系数已含在每日预测销量中

    Args:
        base_daily_sales: 基准销量
        lifecycle: 生命周期阶段
        start: 窗口开始日期（补货到货日）
        end: 窗口结束日期（需求截止日）
        launch_start: 节日「启动期」起始日（启动期逐日指数用；无启动期传 None）

    Returns:
        总预测需求
    """
    days = (end - start).days
    if days <= 0:
        return 0

    # 每日预测求和（已含安全系数）；启动期逐日按「距启动期起始日的经过天数」取指数
    total = 0.0
    for d in range(days):
        elapsed = ((start + timedelta(days=d)) - launch_start).days if launch_start else 0
        total += daily_forecast_by_stage(base_daily_sales, lifecycle, elapsed)

    demand = round(total)
    logger.info(f"窗口需求: 基准={base_daily_sales}, 阶段={lifecycle}, "
                f"启动期起始={launch_start}, 窗口={start}~{end}({days}天), 需求={demand}")
    return demand


def festival_replenishment_plan(
    base_daily_sales: float,
    lifecycle: str,
    available_stock: int,
    lead_time: int,
    festival_date: date,
    is_decoration: bool,
    current_date: date = None,
    launch_start: date = None,
) -> dict:
    """
    节日/主题产品补货计划（需求文档第六章）

    流程:
      1. 计算需求截止日期
      2. 计算库存售卖天数/售罄日期
      3. 计算各运输方式到货日期
      4. 情况一: 库存无法支撑补货周期 → 空运补缺口 + 海运补货
      5. 情况二: 库存可支撑 → 补货数量 = 窗口内预测求和

    Args:
        base_daily_sales: 基准销量（最近3天平均）
        lifecycle: 生命周期阶段
        available_stock: 可使用库存
        lead_time: 大货工期
        festival_date: 节日日期
        is_decoration: 是否装饰类
        current_date: 当前日期
        launch_start: 节日「启动期」起始日（启动期逐日指数用）

    Returns:
        {
            "demand_deadline": str,     # 需求截止日期
            "sellout_date": str,        # 预计售罄日期
            "sellout_days": float,      # 库存售卖天数
            "launch_start": str|None,   # 启动期起始日
            "elapsed_days": int,        # 计算日的启动期经过天数
            "transport_plans": [        # 各运输方式方案
                {"mode": str, "label": str, "arrival": str, "selling_days": int,
                 "qty": int, "gap_qty": int, "is_profitable_first": bool, "note": str}
            ],
            "recommended": {...},       # 推荐方案
            "can_replenish": bool,
            "reason": str,
        }
    """
    current_date = current_date or date.today()
    elapsed_today = (current_date - launch_start).days if launch_start else 0

    # 1. 需求截止日期
    demand_deadline = calc_demand_deadline(festival_date, is_decoration)

    # 2. 库存售卖天数（启动期用「今天距启动期起始日」的经过天数）
    sellout_days = calc_inventory_sellout_days(available_stock, base_daily_sales,
                                               lifecycle, elapsed_today)
    sellout_date = current_date + timedelta(days=sellout_days)

    # 3. 各运输方式方案
    plans = []
    for mode, cfg in TRANSPORT_MODES.items():
        arrival = calc_arrival_date(current_date, lead_time, mode)
        selling_days = (demand_deadline - arrival).days

        # 剩余售卖天数检查（P1）
        ok_remaining, reason_remaining = check_remaining_days(selling_days)

        # 情况判断
        if sellout_date < arrival:
            # 情况一: 库存无法支撑 → 存在缺口（缺口按到货当天的经过天数折算日销）
            gap_days = (arrival - sellout_date).days
            gap_elapsed = (arrival - launch_start).days if launch_start else 0
            gap_qty = round(gap_days * daily_forecast_by_stage(base_daily_sales, lifecycle, gap_elapsed))
            # 海运正常补货（窗口内逐日预测求和）
            sea_qty = forecast_window_demand(base_daily_sales, lifecycle,
                                             arrival, demand_deadline, launch_start)
            qty = sea_qty
            note = (f"库存预计{sellout_date}售罄，存在{gap_days}天缺口，"
                    f"需空运/快递补缺口{gap_qty}件，本渠道补货{qty}件")
        else:
            # 情况二: 库存可支撑补货周期
            gap_qty = 0
            qty = forecast_window_demand(base_daily_sales, lifecycle,
                                         arrival, demand_deadline, launch_start)
            note = f"库存可支撑至{sellout_date}，本渠道窗口{selling_days}天，补货{qty}件"

        if not ok_remaining:
            qty = 0
            note = reason_remaining

        plans.append({
            "mode": mode,
            "label": cfg["label"],
            "arrival": arrival.isoformat(),
            "selling_days": max(selling_days, 0),
            "qty": qty,
            "gap_qty": gap_qty,
            "transport_days": get_transport_days(mode, current_date),
            "freight_fee": get_transport_fee(mode, current_date),
            "note": note,
        })

    # 4. 推荐方案: 优先海运（成本低），缺口用空运/快递
    recommended = None
    can_replenish = False
    for p in plans:
        if p["qty"] > 0 and p["selling_days"] >= MIN_SELLING_DAYS:
            if recommended is None or p["mode"] == "sea":
                recommended = p
                can_replenish = True

    if not recommended:
        reason = "所有运输方式均无法满足剩余售卖天数≥14天或存在Profit风险"
    else:
        reason = recommended["note"]

    return {
        "demand_deadline": demand_deadline.isoformat(),
        "sellout_date": sellout_date.isoformat(),
        "sellout_days": round(sellout_days, 1),
        "launch_start": launch_start.isoformat() if launch_start else None,
        "elapsed_days": elapsed_today,
        "transport_plans": plans,
        "recommended": recommended,
        "can_replenish": can_replenish,
        "reason": reason,
    }


# ==================== 长期产品 ====================

def long_term_replenishment_plan(
    base_daily_sales: float,
    available_stock: int,
    lead_time: int,
    safety_factor: float = 1.2,
    current_date: date = None,
) -> dict:
    """
    长期产品补货计划（需求文档第六章 长期产品）

    流程:
      1. 预测日销 = 基准销量 × 安全系数（安全系数用于预测销量）
      2. 库存售卖天数 = 可用库存 ÷ 预测日销
      3. 补货周期 = 大货工期 + 运输时间（默认海运）
      4. 补货数量 = 预测日销 × 补货周期
      5. 缺货风险: 缺口 = (到货-售罄) × 预测日销 → 空运/快递补缺口

    Args:
        base_daily_sales: 基准销量（最近3天平均）
        available_stock: 可使用库存
        lead_time: 大货工期
        safety_factor: 安全系数（默认1.2）
        current_date: 当前日期

    Returns:
        {
            "sellout_days": float,     # 库存售卖天数
            "sellout_date": str,       # 预计售罄日期
            "replenishment_cycle": int,# 补货周期
            "base_qty": int,           # 基础补货数量
            "final_qty": int,          # 最终补货数量
            "gap_qty": int,            # 缺货缺口
            "gap_mode": str,           # 补缺口运输方式
            "can_replenish": bool,
            "reason": str,
        }
    """
    current_date = current_date or date.today()

    # 1. 预测日销（含安全缓冲）= 基准销量 × 安全系数
    forecast_daily = base_daily_sales * safety_factor

    # 2. 库存售卖天数 = 可用库存 ÷ 预测日销
    sellout_days = available_stock / forecast_daily if base_daily_sales > 0 else 999
    sellout_date = current_date + timedelta(days=sellout_days)

    # 3. 补货周期（按工期取默认值: 7-10天→45, 11-20天→50, 21天+→60）
    #    文档: 默认补货周期已含生产+运输，直接使用
    replenishment_cycle = LONG_TERM_DEFAULT_CYCLE.get(
        next(((lo, hi) for lo, hi in LONG_TERM_DEFAULT_CYCLE if lo <= lead_time <= hi), (1, 10)),
        45,
    )

    # 4. 补货数量 = 预测日销(基准×安全系数) × 补货周期
    base_qty = round(forecast_daily * replenishment_cycle)
    final_qty = base_qty

    # 5. 缺货风险判断
    arrival = current_date + timedelta(days=replenishment_cycle)
    gap_qty = 0
    gap_mode = ""
    if sellout_date < arrival:
        gap_days = (arrival - sellout_date).days
        gap_qty = round(gap_days * forecast_daily)
        # 判断空运/快递是否能补上
        for mode in ("air", "express"):
            if current_date + timedelta(days=lead_time + get_transport_days(mode, current_date)) <= sellout_date:
                gap_mode = TRANSPORT_MODES[mode]["label"]
                break
        if not gap_mode:
            gap_mode = "无法弥补"

    can_replenish = gap_qty == 0 or bool(gap_mode and gap_mode != "无法弥补")
    reason = (f"库存售卖{round(sellout_days, 1)}天(至{sellout_date}), "
              f"默认补货周期{replenishment_cycle}天, "
              f"预测日销{forecast_daily:.1f}(基准×安全系数{safety_factor}), 补货{final_qty}")

    return {
        "sellout_days": round(sellout_days, 1),
        "sellout_date": sellout_date.isoformat(),
        "replenishment_cycle": replenishment_cycle,
        "forecast_daily": round(forecast_daily, 2),
        "safety_factor": safety_factor,
        "base_qty": base_qty,
        "final_qty": final_qty,
        "gap_qty": gap_qty,
        "gap_mode": gap_mode,
        "can_replenish": can_replenish,
        "reason": reason,
    }


# 兼容旧接口: 简化版补货数量计算
def forecast_stage_demand(base_daily_sales: float, lifecycle: str,
                          days: int, elapsed_days: int = 0) -> float:
    """计算剩余销售周期内的总预测需求（简化版，兼容调用；安全系数已含在每日预测销量中）"""
    if days <= 0:
        return 0
    total = sum(
        daily_forecast_by_stage(base_daily_sales, lifecycle, elapsed_days + d)
        for d in range(days)
    )
    return round(total)


def forecast_long_term_demand(base_daily_sales: float, lead_time: int,
                              safety_factor: float = 1.2) -> int:
    """长期产品补货数量（简化版: 预测日销=基准×安全系数, 再×默认周期）"""
    cycle = LONG_TERM_DEFAULT_CYCLE.get(
        next(((lo, hi) for lo, hi in LONG_TERM_DEFAULT_CYCLE if lo <= lead_time <= hi), (1, 10)),
        45,
    )
    return round(base_daily_sales * safety_factor * cycle)


def round_to_box(qty: int, box_qty: int = 1) -> int:
    """按装箱数量向上取整"""
    if box_qty <= 0:
        return qty
    return int(math.ceil(qty / box_qty) * box_qty)
