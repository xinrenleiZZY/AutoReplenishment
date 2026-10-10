"""采购触发与建议量（C1 拆分批3 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
含：节日窗口可赶性判定、采购触发（含节日窗口/长期产品分支）、到货前可售模拟、
    缺口诊断落盘、建议量 v1/v2。
对外入口不变：app.tasks.calculation_tasks.<name> 仍可用（原文件显式导入本模块）。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta
from math import ceil

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.product import Product
from app.services import new_product_policy
from app.services.calc.common import _PID_DIR, _safe_int
from app.services.calc.forecast import _month_end
from app.services.calc.inventory import _old_product_inventory_days

logger = logging.getLogger(__name__)


def _festival_window_air_catchable(festival_window: dict | None, lead_time: int) -> bool:
    """窗口逻辑覆盖时间轴门禁：判断空运是否仍能赶上热卖月（T1-9）。

    仅对老品节日产品启用：当窗口剩余需求 > 0 且空运到仓仍能覆盖未来的热卖月销售时，
    允许用空派保热卖；否则维持时间轴门禁的禁止采购。

    判定口径（与 _calc_purchase_trigger 的窗口触发保持一致）：
      - 热卖结束月（hot_end_month）须未完全过去（>= 今天所在月）；
      - 今天 + (大货工期 + 空运天数) 的到仓月须 <= 热卖结束月（空运到仓仍能覆盖热卖月）。
    """
    if not festival_window:
        return False
    remaining = festival_window.get("remaining") or 0
    if remaining <= 0:
        return False
    today = date.today()
    hot_end = int(festival_window.get("hot_end_month") or 0)
    if not hot_end or hot_end < today.month:
        return False  # 热卖月已完全过去，空运也赶不上
    is_peak = 8 <= today.month <= 12
    air_days = settings.AIR_PEAK_DAYS if is_peak else settings.AIR_SLOW_DAYS
    arrival = today + timedelta(days=(lead_time or 30) + air_days)
    return arrival.month <= hot_end


def _calc_purchase_trigger(forecast: dict, inventory: dict, product: Product, lead_time: int) -> dict:
    """计算采购触发判断（需求文档第九章；仅老品，新品走 _run_new_product_flow）

    库存天数（老品唯一口径）：窗口「各月预估 ÷ 该月天数 → 按库存坐落月逐月扣减可用库存」
      - 节日产品 = 节日窗口（去年同窗口各月 × 趋势系数）
      - 长期产品 = 长期窗口（[明天, 当前月+2 月末]），见下方对应分支
      - 无各月占比（去年同窗口无销量）→ 不计算，库存天数留空（None）
    不再使用「近3天日均」「30天销量÷30×趋势修正系数」等旧口径。
    数据过少（近3天无销量/数据覆盖<20天）时沿用领星可售天数口径。
    补货周期 = 大货工期 + 运输时间（海运淡季30/旺季45、空派淡季10/旺季15、快递淡季3/旺季6）
    库存售卖天数 < 补货周期(海运) → 建议采购；越短周期越紧急（空派/快递）
    """
    available_stock = inventory.get("available_stock", 0)
    thirty_volume = inventory.get("thirty_volume") or forecast.get("thirty_volume") or 0
    average_thirty_volume = inventory.get("average_thirty_volume") or 0
    recent_3_days_avg = inventory.get("recent_3_days_avg") or 0
    life_cycle = inventory.get("life_cycle") or product.life_cycle

    # 数据过少（近3天无销量/数据覆盖<20天）：库存天数直接用领星口径，不再重算
    if inventory.get("lx_used"):
        inventory_days = inventory.get("inventory_days")
        base_daily = recent_3_days_avg or 0
        daily_sales = base_daily
        daily_desc = "(领星口径)"
    else:
        # 库存天数不使用任何日均口径，统一由下方「唯一口径」落值
        base_daily = recent_3_days_avg or average_thirty_volume or (thirty_volume / 30 if thirty_volume > 0 else 0)
        daily_sales = 0
        inventory_days = None
        daily_desc = "(老品库存天数仅按窗口各月占比逐月扣减口径)"

    # ── 库存天数唯一口径（与 Step 6 同源）：窗口「各月预估 ÷ 该月天数 → 逐月扣减」──
    #    有窗口（节日优先 / 长期产品）→ 覆盖天数；无各月占比 → 留空（None）；
    #    无窗口（老品无节日且非长期）→ 留空、不触发采购（保持 None，不另立口径）。
    #    数据过少的产品走上方领星分支，不用窗口口径。
    _cov = _old_product_inventory_days(inventory, inventory.get("festival_window"))
    if _cov["source"] == "window":
        inventory_days = _cov["days"]
        daily_desc = ""

    # 库存天数上限：与库存分析一致，防止失真大值（如21111天）污染积压提醒
    if inventory_days is not None:
        inventory_days = min(inventory_days, 365)
    # 更新库存覆盖天数
    inventory["inventory_days"] = inventory_days
    if _cov["source"] == "window":
        inventory["inventory_days_source"] = "window"
        inventory["inventory_days_formula"] = _cov["formula"]

    # 断货兜底：可用库存=0 且无有效库存天数（无销量/无领星可售天数）→ 视为0天触发采购
    # 仅当存在实际需求（近3天/30天销量或未来预测>0）；无需求产品（0销量0预测）不触发，
    # 避免"需要采购但建议0件"矛盾（如 0 销量零库存老品）
    has_demand = (
        base_daily > 0
        or (thirty_volume or 0) > 0
        or (average_thirty_volume or 0) > 0
        or (forecast.get("forecast_total") or 0) > 0
    )
    if inventory_days is None and (available_stock or 0) <= 0 and has_demand:
        inventory_days = 0
        inventory["inventory_days"] = 0

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

    # ── 老品节日产品：按节日窗口同期增长预估触发（替代库存天数判断，T1） ──
    #    窗口剩余需求 > 0（当年窗口按 G 放大后，库存+已售+在途仍不足）→ 需要采购；
    #    remaining == 0 → 窗口已被覆盖，无需采购。非节日且无窗口记录时走下方长期窗口/统一触发判断。
    festival_window = inventory.get("festival_window")
    if festival_window:                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         
        remaining = festival_window.get("remaining") or 0
        window_months = festival_window.get("window_months") or []                                                                                      
        window_label = f"{window_months[0]}~{window_months[-1]}月" if window_months else "?"
        hot_end = int(festival_window.get("hot_end_month") or 0)
        if remaining > 0:
            # 库存天数 = 统一口径结果（_old_product_inventory_days，与 Step 6 同源）：
            # 「窗口各月预估 ÷ 该月天数 → 逐月扣减可用库存」，反映库存坐落在哪个月、能卖到哪天；
            # 无各月占比 → 留空（None）。
            est = festival_window.get("window_estimate") or 0
            stock = int(inventory.get("available_stock") or 0)
            inventory_days = _cov["days"]
            inventory["inventory_days"] = inventory_days
            # 同期增长 = 趋势系数 − 1（已按上限 1.5 封顶）；被封顶时标注原值
            _g = float(festival_window.get("g") or 0)
            _g_raw = festival_window.get("g_raw")
            _growth = f"{int(round(_g * 100))}%同期增长"
            if _g_raw is not None and abs(float(_g_raw) - _g) > 1e-9:
                _growth += f"（原值：{int(round(float(_g_raw) * 100))}%）"
            reason = (f"节日窗口[{window_label}]预估总需求{est}（{_growth}），"
                      f"库存{stock}＋在途缺口{remaining}；{_cov['formula']}，需提前补货")
            recommended = "海运"
            if hot_end and hot_end > date.today().month and hot_end - date.today().month <= 2:
                recommended = "空派"  # 热卖月临近，海运恐赶不上，需空派保热卖
            return {
                "purchase_trigger": "需要采购",
                "reason": reason,
                "inventory_days": inventory_days,
                "replenishment_cycle": sea_cycle,
                "transport_cycles": transport_cycles,
                "recommended_transport": recommended,
            }
        # 窗口已被现有库存+在途覆盖：无需采购
        reason = f"节日窗口[{window_label}]预估总需求{festival_window.get('window_estimate') or 0}，现有库存＋在途已覆盖，无需采购"
        return {
            "purchase_trigger": "无需采购",
            "reason": reason,
            "inventory_days": inventory_days,
            "replenishment_cycle": sea_cycle,
            "transport_cycles": transport_cycles,
            "recommended_transport": "无需采购",
        }

    # ── 老品长期产品：与节日老品同口径（窗口各月占比逐月扣减） ──
    #    [明天, 当前月+2 月末] 各月预估 = 去年该月 × 趋势系数，÷ 该月天数 → 逐月扣减可用库存；
    #    库存天数 = 覆盖天数（已在 _cov 落值），再走下方统一触发判断（> 补货周期 → 无需采购）。
    #    无各月占比（去年同窗口无销量）→ 不计算，库存天数留空（None）。
    window_note = _cov["formula"] if _cov["source"] == "window" else ""

    # 触发条件：库存覆盖天数 ≤ 海运补货周期(+安全库存)；等于周期时也触发（否则到货即售罄，无缓冲）
    safe_days = int(settings.SAFE_STOCK_DAYS or 0)
    trigger_line = sea_cycle + safe_days
    _prefix = f"{window_note}；" if window_note else ""
    if inventory_days is not None and inventory_days <= trigger_line:
        if inventory_days < express_cycle:
            recommended = "快递"
        elif inventory_days < air_cycle:
            recommended = "空派"
        else:
            recommended = "海运"
        return {
            "purchase_trigger": "需要采购",
            "reason": f"{_prefix}库存仅覆盖{inventory_days}天{daily_desc}，不超过补货周期{sea_cycle}天(工期{lead_time}+海运{sea_days})，建议{recommended}（空派{air_cycle}天/快递{express_cycle}天）",
            "inventory_days": inventory_days,
            "replenishment_cycle": sea_cycle,
            "transport_cycles": transport_cycles,
            "recommended_transport": recommended,
        }
    elif inventory_days is not None:
        return {
            "purchase_trigger": "无需采购",
            "reason": f"{_prefix}库存可覆盖{inventory_days}天{daily_desc}，高于补货周期{sea_cycle}天(工期{lead_time}+海运{sea_days})",
            "inventory_days": inventory_days,
            "replenishment_cycle": sea_cycle,
            "transport_cycles": transport_cycles,
            "recommended_transport": "无需采购",
        }
    return {
        "purchase_trigger": "无需采购",
        "reason": (f"{_prefix}无有效库存天数（无窗口各月占比或领星未提供可售天数），"
                   f"暂不触发采购；补货周期{sea_cycle}天(工期{lead_time}+海运{sea_days})"),
        "inventory_days": None,
        "replenishment_cycle": sea_cycle,
        "transport_cycles": transport_cycles,
        "recommended_transport": "无需采购",
    }


def _simulate_sellable_before_arrival(forecast: dict, inventory: dict) -> dict:
    """模拟到货前可售数量（老品可售库存缺口数量专用，仅写本地 json 备查，不落库）

    取「窗口首日 → FBA在途到货日」区间内的预估销量之和：
      各月日均 = 该月预估销量 ÷ 该月在窗口内的天数
        （首月按窗口首日→月末的剩余天数，末月按窗口结束日截断）
      到货前天数 = 该月内落在到货日之前的天数
    到货缺失 / 到货不晚于窗口首日 / 无未来月度预测 / 无节日窗口 → 0（无缺口风险）。

    返回 {"qty": float, "arrival_days": int | None, "rows": [各月明细]}
    """
    months = forecast.get("forecast_months") or []
    fw = inventory.get("festival_window") or {}
    win_start_s = fw.get("win_start")
    win_end_s = fw.get("win_end")
    arrival_s = inventory.get("inbound_arrival_date")
    result = {"qty": 0.0, "arrival_days": None, "rows": []}
    if not months or not win_start_s or not win_end_s or not arrival_s:
        return result
    try:
        win_start = date.fromisoformat(str(win_start_s))
        win_end = date.fromisoformat(str(win_end_s))
        arrival = date.fromisoformat(str(arrival_s))
    except (ValueError, TypeError):
        return result
    result["arrival_days"] = (arrival - date.today()).days    # FBA在途到货时间天数（从今天起算）
    if arrival <= win_start:                                  # 到货早于窗口首日，到货前不产生需求
        return result
    total = 0.0
    rows = []
    for item in months:
        try:
            _y, _m = str(item.get("month", "")).split("-")
            m_first = date(int(_y), int(_m), 1)
        except (ValueError, AttributeError):
            continue
        m_start = max(m_first, win_start)
        m_end = min(_month_end(m_first), win_end)
        if m_start > m_end:
            continue
        span = (m_end - m_start).days + 1                      # 该月在窗口内的天数
        qty = float(item.get("forecast_qty") or 0)
        daily = qty / span if span > 0 else 0.0                # 该月日均
        seg_end = min(m_end, arrival)
        if seg_end < m_start:                                  # 整个月都排在到货日之后
            continue
        seg_days = (seg_end - m_start).days + 1                # 到货前落在该月的天数
        part = daily * seg_days
        total += part
        rows.append({
            "month": item.get("month"),
            "该月预估": qty,
            "窗口内天数": span,
            "日均": round(daily, 2),
            "到货前天数": seg_days,
            "到货前销量": round(part, 1),
        })
    result["qty"] = round(total, 1)
    result["rows"] = rows
    return result


def _dump_sellable_gap_sim(asin: str, inventory: dict, m: dict) -> None:
    """把「模拟到货前可售数量／可售缺口数量」写入本地 json 备查（暂不落库）

    路径与 p_id/sif_lifecycle 一致：p_id/sellable_gap/{asin}.json
    （容器内 /app/p_id 已挂载到宿主机 ./p_id，写文件即可在宿主机直接查看）
    """
    if not asin:
        return
    try:
        out_dir = os.path.join(_PID_DIR, "sellable_gap")
        os.makedirs(out_dir, exist_ok=True)
        record = {
            "asin": asin,
            "calc_date": date.today().isoformat(),
            "可用库存": m.get("available_stock"),
            "到货日": inventory.get("inbound_arrival_date"),
            "到货天数": m.get("arrival_days"),
            "模拟到货前可售数量": m.get("simulated_sellable_qty"),
            "可售缺口数量": round(m.get("sellable_gap_qty") or 0, 1),
            "缺口豁免(快递+空运均有利润)": bool(m.get("gap_exempt")),
            "缺口是否已从建议量扣减": not bool(m.get("gap_exempt")),
            "基础建议(suggested_raw)": (
                round(m["suggested_raw"], 1) if m.get("suggested_raw") is not None else None
            ),
            "预测未来销量(demand)": m.get("demand"),
            "各月模拟明细": m.get("simulated_rows") or [],
        }
        with open(os.path.join(out_dir, f"{asin}.json"), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # 备查文件写入失败不影响主流程
        logger.warning(f"写入可售缺口模拟文件失败 asin={asin}: {exc}")


def _base_suggested_gap(forecast: dict, inventory: dict, product: Product) -> dict:
    """基础建议量与可售库存缺口的公共计算

    供 _calc_suggested_qty 与「长期产品库存充足拦截」共用，保证两处口径一致。

    可售库存缺口数量（老品口径）：
      模拟到货前可售数量 = 窗口首日 → FBA在途到货日 区间内各月预估销量之和
        （各月预估 ÷ 该月天数 得日均，首月按窗口首日→月末的剩余天数）
      可售库存缺口数量 = max(0, 模拟到货前可售数量 − 可用库存)

    返回字段：
      demand                生命周期预测未来销量
      available_stock       可使用库存（已含 FBA可售/预留/在途/本地/采购待到货）
      first_month_forecast_qty 首月（预测首月）预测销量
      arrival_days          FBA在途到货时间天数（从今天起算）
      simulated_sellable_qty 模拟到货前可售数量（仅本地 json 备查，不落库）
      simulated_rows        模拟到货前各月明细
      sellable_gap_qty      可售库存缺口数量
      gap_exempt            缺口豁免标记（快递+空运成本表均有利润 → True）
      suggested_raw         基础建议 = demand - available_stock - sellable_gap_qty
                            （gap_exempt 为 True 时不扣减 sellable_gap_qty）
    """
    months = forecast.get("forecast_months") or []
    three_month_demand = sum(m.get("forecast_qty", 0) for m in months[:3])
    # 实际日均销量兜底：近3天平均 > 30天平均 > 30天总量/30
    daily = max(
        float(inventory.get("recent_3_days_avg") or 0),
        float(inventory.get("average_thirty_volume") or 0),
        float(inventory.get("thirty_volume") or 0) / 30.0,
    )
    cycle = float(inventory.get("replenishment_cycle") or 0) or 30.0
    demand = max(three_month_demand, round(daily * cycle))
    # 预测明细缺失且无实际销量时，用最近30天销量×3个月兜底
    if demand <= 0:
        thirty_volume = inventory.get("thirty_volume") or 0
        if thirty_volume and thirty_volume > 0:
            demand = int(thirty_volume) * 3

    available_stock = inventory.get("available_stock", 0)
    first_month_forecast_qty = float((months[0].get("forecast_qty") if months else 0) or 0)  # 首月预测销量

    # ── 可售库存缺口数量 = max(0, 模拟到货前可售数量 − 可用库存) ──
    sim = _simulate_sellable_before_arrival(forecast, inventory)
    simulated_sellable_qty = sim["qty"]
    sellable_gap_qty = max(0.0, simulated_sellable_qty - float(available_stock or 0))

    # ── 缺口扣减豁免：快递、空运成本表均有利润时，可用空运/快递在到货前补齐缺口，
    #    故不再扣减缺口数量（建议量 = 预测销量 − 可用库存）。
    #    仅当「快递(express)」「空运(air)」两渠道 Profit 均 >0 才豁免；否则维持原公式扣减。
    gap_exempt = False
    try:
        _ct = new_product_policy.calc_cost_table(product)
        _profitable = set(_ct.get("profitable_modes") or [])
        gap_exempt = "express" in _profitable and "air" in _profitable
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{getattr(product, 'asin', '')}] 缺口豁免成本表计算失败: {exc}")

    return {
        "demand": demand,
        "available_stock": available_stock,
        "first_month_forecast_qty": first_month_forecast_qty,
        "arrival_days": sim["arrival_days"],
        "simulated_sellable_qty": simulated_sellable_qty,
        "simulated_rows": sim["rows"],
        "sellable_gap_qty": sellable_gap_qty,
        "gap_exempt": gap_exempt,
        "suggested_raw": demand - available_stock - (0.0 if gap_exempt else sellable_gap_qty),
    }


def _calc_suggested_qty(forecast: dict, inventory: dict, product: Product) -> int:
    """计算建议采购数量（需补货数量）

    需补货数量 = 生命周期预测未来销量 - 可使用库存 - 可售库存缺口数量
      - 生命周期预测未来销量 demand = max(未来3个月预测需求, 实际日均销量 × 补货周期)
        —— 解决"触发需要采购（库存天数<周期）但建议量为0"的矛盾：
        库存只够 N 天而补货周期更长时，必须按周期内需求补足缺口，而不能只看3个月预测。
      - 可使用库存 = FBA可售 + FBA预留 + FBA在途 + 本地库存 + 采购待到货
        （_analyze_inventory.available_stock，已含本地/待到货，无需重复扣减）
      - 可售库存缺口数量 = max(0, 模拟到货前可售数量 − 可用库存)
          模拟到货前可售数量 = 窗口首日 → FBA在途到货日 区间内各月预估销量之和
          （各月预估 ÷ 该月天数 得日均，首月按窗口首日→月末的剩余天数）
          该值仅写本地 json 备查（p_id/sellable_gap/{asin}.json），暂不落库
      - 缺口扣减豁免：快递、空运成本表均有利润时，不扣减可售库存缺口数量
    """
    m = _base_suggested_gap(forecast, inventory, product)
    # 计算字段落回 inventory，供记录/展示
    inventory["first_month_forecast_qty"] = m["first_month_forecast_qty"]
    inventory["arrival_days"] = m["arrival_days"]
    inventory["sellable_gap_qty"] = round(m["sellable_gap_qty"], 1)
    inventory["gap_exempt"] = m["gap_exempt"]
    # 模拟到货前可售数量：仅写本地 json 备查，暂不落库
    _dump_sellable_gap_sim(getattr(product, "asin", ""), inventory, m)

    # 需补货数量 = 生命周期预测未来销量 - 可使用库存 - 可售库存缺口数量
    suggested = m["suggested_raw"]

    if suggested <= 0:
        return 0

    box_qty = _safe_int(product.box_quantity, 1)
    min_order_qty = _safe_int(product.min_order_qty, 0)
    # 向上取整至装箱数量倍数（如 10000/300=33.33 → 34箱）
    suggested = ceil(suggested / box_qty) * box_qty

    # 不低于最低采购量
    if min_order_qty > 0 and suggested < min_order_qty:
        suggested = min_order_qty

    return suggested


async def _calc_suggested_qty_v2(forecast: dict, inventory: dict, product: Product,
                                 lead_time: int, life_cycle: str, session: AsyncSession) -> tuple:
    """计算建议采购数量（主流程整合 v3：接入 new_product_policy 节日/长期补货策略）

    主建议量 = 未来3个月需求 - 当前库存（向上取整箱规倍数，用户指定规则）
    节日产品: 额外用 festival_replenishment_plan 计算销售窗口补货量，取较大值
    生命周期采购策略:
      启动期 → 小批测试（上限=未来1个月需求）
      增长期 → 逐步增加采购（上限=未来2个月需求）
      热卖期 → 保证不断货（维持按补货周期补足）
      成熟期 → 稳定补货（维持基础公式）
      下降期 → 减少采购/不采购（建议量归0）
    返回 (建议数量, 策略说明)
    """
    base_qty = _calc_suggested_qty(forecast, inventory, product)
    note = ""

    # ── 生命周期采购策略 ──
    months = forecast.get("forecast_months") or []
    forecast_total = forecast.get("forecast_total") or 0
    box_qty = _safe_int(product.box_quantity, 1)
    min_order_qty = _safe_int(product.min_order_qty, 0)
    daily = max(
        float(inventory.get("recent_3_days_avg") or 0),
        float(inventory.get("average_thirty_volume") or 0),
        float(inventory.get("thirty_volume") or 0) / 30.0,
    )
    if base_qty > 0:
        if life_cycle == "启动期":
            # 小批测试：上限=未来1个月需求（至少1箱/最低采购量）
            one_month = (months[0].get("forecast_qty") if months else 0) or round(forecast_total / 6) or round(daily * 30)
            cap = max(box_qty, min_order_qty, one_month)
            if base_qty > cap:
                base_qty = cap
                note = f"生命周期启动期→小批测试，建议量上限未来1个月需求{one_month}"
        elif life_cycle == "增长期":
            # 逐步增加采购：上限=未来2个月需求
            two_month = (sum(m.get("forecast_qty", 0) for m in months[:2])
                         or round(forecast_total / 3) or round(daily * 60))
            cap = max(box_qty, min_order_qty, two_month)
            if base_qty > cap:
                base_qty = cap
                note = f"生命周期增长期→逐步增加采购，建议量上限未来2个月需求{two_month}"
        elif life_cycle == "下降期":
            # 下降期：95%不采购，除非巨大利润（海运毛利率 ≥ decline_profit_threshold）
            try:
                from app.services.config_service import get_param

                decline_threshold = float(await get_param(session, "decline_profit_threshold") or 0.35)
            except Exception:  # noqa: BLE001
                decline_threshold = 0.35
            try:
                _ct = new_product_policy.calc_cost_table(product)
                sea_margin = (_ct.get("channels") or {}).get("sea", {}).get("margin") or 0.0
            except Exception:  # noqa: BLE001
                sea_margin = 0.0
            if sea_margin >= decline_threshold:
                note = (f"生命周期下降期但海运毛利率{sea_margin:.1%}≥{decline_threshold:.0%}"
                        "（巨大利润），允许采购")
            else:
                base_qty = 0
                note = (f"生命周期下降期→减少采购/不采购（海运毛利率{sea_margin:.1%}"
                        f"<{decline_threshold:.0%}巨大利润阈值），建议量置0")

    # ── 老品节日产品：用节日窗口同期增长预估替代原 festival_replenishment_plan（T1） ──
    #    窗口剩余需求（按 G 放大后的总需求 − 已售 − 库存 − 在途）为权威建议来源；
    #    非节日/无窗口记录（festival_window=None）时回退基础建议，不再调用旧节日策略。
    festival_window = inventory.get("festival_window")
    if product.festival and festival_window:
        remaining = festival_window.get("remaining") or 0
        if remaining > 0:
            box_qty = _safe_int(product.box_quantity, 1)
            window_qty = new_product_policy.round_to_box(remaining, box_qty)
            if window_qty > base_qty:
                base_qty = window_qty
                note = (f"节日窗口同期增长预估剩余需求{remaining}（箱规{box_qty}取整→{window_qty}）"
                        f"> 基础建议，采用窗口量")
            else:
                note = f"节日窗口剩余需求{remaining} ≤ 基础建议{base_qty}，采用基础建议"

    return base_qty, note


