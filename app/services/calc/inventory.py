"""库存 / 裁决 / 节日窗口 / 新品流程（C1 拆分批3 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
含：生命周期识别、库存分析（四套口径）、裁决（verdict 归一与仲裁）、第二高峰临近补货、
    新品硬规则与长期库存覆盖、新品流程、节日/长期窗口需求与库存覆盖天数。
对外入口不变：app.tasks.calculation_tasks.<name> 仍可用（原文件显式导入本模块）。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.daily_sales_stat import DailySalesStat
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.models.sales import SalesData
from app.services import new_product_policy
from app.services.sales_fallback import get_daily_sales_dual, sum_daily_sales_dual
from app.services.semantic_classify import get_semantic_classification
from app.services.time_axis import get_festival_info
from app.services.calc.common import (
    SECOND_PEAK_APPROACH_DAYS,
    _fetch_acos,
    _get_new_product_cfg,
    _recent_daily_orders,
    _safe_float,
    _safe_int,
)
from app.services.calc.forecast import TREND_COEFF_MAX, _festival_sales_window, _month_end
from app.services.calc.score import _plan_batches

if TYPE_CHECKING:  # 仅类型标注用；避免 calc ↔ calculation_tasks 运行期循环导入
    from app.tasks.calculation_tasks import StepRecorder

logger = logging.getLogger(__name__)


async def _identify_lifecycle(product: Product, session: AsyncSession) -> str:
    """识别产品生命周期：节日时间点表优先（节日+当前时间）

    - 长期产品（product_type=长期产品）：全年销售不过季，固定默认「热卖期」；
    - 有节日（listing 标签/核心销售月份）：按 festival_calendar 阶段区间判定，
      当前日期不在任何阶段区间 → 「下降期」；
    - 非节日产品（festival 为空且非长期产品）：标签缺少导致无分类，保持为空。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return "热卖期"
    from app.services.festival_lifecycle import lifecycle_by_festival, resolve_festival

    try:
        # 非节日产品（festival 为空且非长期产品）：标签缺少导致无分类，保持为空
        if not await resolve_festival(product, session):
            return ""
        fc = await lifecycle_by_festival(product, session)
        if fc:
            return fc
    except Exception as e:  # noqa: BLE001
        logger.warning("[%s] 节日时间点表生命周期判定失败: %s", getattr(product, "asin", "?"), e)
    # 边界：节日产品当前日期不在任何阶段区间 → 下降期
    return "下降期"


async def _analyze_inventory(asin: str, session: AsyncSession) -> dict:
    """分析库存健康（最新数据优先：products 基础数据 → 历史快照兜底）

    任何一次拉取（FBA库存/待到货/产品导入）都会写回 products 基础数据，
    分析直接读 products 的最新值；快照仅作字段缺失时的历史兜底，避免用旧快照。
    """
    today = date.today()

    prod = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    snap = (await session.execute(
        select(InventorySnapshot)
        .where(InventorySnapshot.asin == asin)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    def _pick(prod_val, snap_val, default=0):
        """products（最新）优先，缺失回退最近快照，再缺失用默认值"""
        if prod_val is not None:
            return prod_val
        if snap_val is not None:
            return snap_val
        return default

    # 在途库存：products 的 FBA 在途/入库中/待发货 汇总；缺失回退快照在途
    prod_inbound = None
    if prod is not None and any(v is not None for v in (
            prod.afn_inbound_shipped_quantity,
            prod.afn_inbound_working_quantity,
            prod.afn_inbound_receiving_quantity)):
        prod_inbound = (_safe_int(prod.afn_inbound_shipped_quantity)
                        + _safe_int(prod.afn_inbound_working_quantity)
                        + _safe_int(prod.afn_inbound_receiving_quantity))
    snap_inbound = None
    if snap is not None and (snap.fba_inbound is not None or snap.fba_inbound_shipped is not None):
        snap_inbound = _safe_int(snap.fba_inbound) + _safe_int(snap.fba_inbound_shipped)

    fba_available = _pick(
        prod.afn_fulfillable_quantity if prod else None,
        snap.fba_available if snap else None,
    )
    fba_reserved = _pick(
        prod.afn_reserved_quantity if prod else None,
        snap.fba_reserved if snap else None,
    )
    fba_inbound = _pick(prod_inbound, snap_inbound)
    # 待调仓 / 调仓中：仅 products 表提供（库存快照无对应字段），缺失按 0
    fba_fc_transfers = _safe_int(prod.reserved_fc_transfers) if prod else 0
    fba_fc_processing = _safe_int(prod.reserved_fc_processing) if prod else 0

    if prod is None and snap is None:
        return {
            "available_stock": 0,
            "inventory_days": 0,
            "replenishment_cycle": 30,
            "urgency_score": 0,
            "thirty_volume": 0,
            "average_thirty_volume": 0,
            "fba_available_days": None,
            "stockout_date": None,
            "estimated_daily_sales": None,
        }

    fba_only = fba_available
    local_stock = _pick(
        prod.quantity if prod else None,
        snap.local_stock if snap else None,
    )
    purchase_on_order = _pick(
        prod.purchase_on_order if prod else None,
        snap.purchase_on_order if snap else None,
    )
    # 可用库存 = FBA可售 + FBA预留 + FBA在途 + 待调仓 + 本地库存 + 采购待到货
    # 「调仓中」不再单列：FBA预留(afn_reserved_quantity)已包含调仓中，另加会重复计算；
    # 「待调仓」不在预留内，需补入。本地库存与采购待到货同样计入可用库存。
    available = (fba_available + fba_reserved + fba_inbound
                 + fba_fc_transfers + local_stock + purchase_on_order)

    # 最近一次日销量快照（30天销量/日均，供触发与建议数量使用）
    snap2 = (await session.execute(
        select(DailySalesSnapshot)
        .where(DailySalesSnapshot.asin == asin)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    # 最近3天平均日销量（文档规则：基准销量 = 最近3天平均日销量，取自 sales_data 明细）
    recent_3_days_avg = 0.0
    today = date.today()
    start3 = today - timedelta(days=2)
    rows3 = (await session.execute(
        select(SalesData.date, SalesData.sales_qty)
        .where(SalesData.asin == asin, SalesData.date >= start3)
        .order_by(SalesData.date)
    )).all()
    if not rows3:
        # 双源优先：daily_sales_stats（逐日实抓）优先，缺时回退 sales_data
        fb3 = await get_daily_sales_dual(asin, start3, today, session, use_zero=True)
        rows3 = [(d, q) for d, q in sorted(fb3.items())]
    if rows3:
        recent_3_days_avg = sum(float(r[1] or 0) for r in rows3) / max(len(rows3), 1)

    # 销量数据覆盖天数（数据过少判断：>=20 天才启用系统公式）
    # 优先 daily_sales_stats（逐日实抓完整数据）的覆盖天数；缺时退回 SalesData 覆盖天数
    fb_days = (await session.execute(
        select(func.count(func.distinct(DailySalesStat.stat_date)))
        .where(DailySalesStat.asin == asin)
    )).scalar_one()
    data_days = (await session.execute(
        select(func.count(func.distinct(SalesData.date)))
        .where(SalesData.asin == asin)
    )).scalar_one()
    if (fb_days or 0) > (data_days or 0):
        data_days = fb_days
        logger.info(f"[{asin}] 销量覆盖天数优先 daily_sales_stats: {data_days}")

    # 领星口径（交叉验证/数据过少兜底）
    lx_days_raw = _pick(
        prod.fba_available_days if prod else None,
        snap.fba_available_days if snap else None,
        default=None,
    )
    lx_days = _safe_int(lx_days_raw)
    lx_daily_raw = _pick(
        prod.estimated_daily_sales if prod else None,
        snap.estimated_daily_sales if snap else None,
        default=None,
    )
    lx_daily = _safe_float(lx_daily_raw)

    # 真实库存覆盖天数 = 可用库存 ÷ 日均销量（日均 = 最近3天平均×生命周期系数，见触发判断）
    # 近30天销量/日均：优先 daily_sales_stats（逐日实抓完整数据），缺时回退日快照（领星聚合口径）
    _win30_start = today - timedelta(days=29)
    _thirty_series = await get_daily_sales_dual(asin, _win30_start, today, session, use_zero=True)
    _thirty_volume = int(sum(_thirty_series.values()) or 0)
    if _thirty_series:
        thirty_volume = _thirty_volume
        average_thirty_volume = round(_thirty_volume / 30.0, 2)
    else:
        thirty_volume = _safe_int(snap2.thirty_volume) if snap2 else 0
        average_thirty_volume = _safe_float(snap2.average_thirty_volume) if snap2 else 0.0

    # 去年同30天销量（评分④同比 G 的基数）：优先 daily_sales_stats 取去年同期窗口，
    # 无节日窗口兜底时这里补齐（节日产品在调用处用 festival_window.last_year_30d 覆盖）
    _ly_start = _win30_start.replace(year=_win30_start.year - 1)
    _ly_end = today.replace(year=today.year - 1)
    _ly_series = await get_daily_sales_dual(asin, _ly_start, _ly_end, session, use_zero=True)
    last_year_thirty_volume = int(sum(_ly_series.values()) or 0)

    # 数据过少（近3天无销量 或 数据覆盖<20天）→ 可用库存/日均销量/库存天数/售罄日全部改用领星口径
    use_formula = recent_3_days_avg > 0 and data_days >= 20
    # 口径溯源（写入步骤 6 判断依据，避免只写"系统公式"分不清用的哪套公式）
    days_formula = ""       # 覆盖天数：用了哪套公式
    stock_formula = ""      # 可用库存：由哪些项相加
    lx_fallback_reason = (
        f"近3天无销量（近3天平均日销={round(recent_3_days_avg, 2)}）"
        if recent_3_days_avg <= 0
        else f"销量数据覆盖仅{data_days}天（<20天）"
    )
    if use_formula:
        if recent_3_days_avg > 0:
            daily_sales = recent_3_days_avg
            daily_formula = f"最近3天平均日销{round(recent_3_days_avg, 2)}"
        elif thirty_volume > 0:
            daily_sales = thirty_volume / 30
            daily_formula = f"近30天销量{thirty_volume}÷30"
        elif average_thirty_volume > 0:
            daily_sales = average_thirty_volume
            daily_formula = f"30天日均销量{average_thirty_volume}"
        else:
            daily_sales = 0
            daily_formula = "无销量数据"
        inventory_days = round(available / daily_sales) if daily_sales > 0 else None
        _raw_days = inventory_days
        if inventory_days is not None:
            inventory_days = min(inventory_days, 365)  # 上限防止失真大值污染积压提醒
        if inventory_days is None:
            days_formula = f"系统公式：无有效日均销量（{daily_formula}），覆盖天数不计算"
        elif _raw_days is not None and _raw_days > inventory_days:
            days_formula = (f"系统公式：可用库存{available}÷{daily_formula}={_raw_days}天，"
                            f"超365天上限截断为{inventory_days}天")
        else:
            days_formula = f"系统公式：可用库存{available}÷{daily_formula}≈{inventory_days}天"
        stock_formula = (f"FBA可售{fba_only}+FBA预留{fba_reserved}+FBA在途{fba_inbound}"
                         f"+待调仓{fba_fc_transfers}"
                         f"+本地库存{local_stock}+采购待到货{purchase_on_order}={available}")
        stockout_date = None  # 公式模式不推算售罄日，售罄日以领星字段为准（交叉验证展示）
        lx_used = False
    else:
        # 领星口径：可用库存 = FBA可售 + 在途 + 待调仓 + 调仓中 + 本地库存 + 采购待到货
        # （避免在途300被误判为0库存断货触发采购）
        available = (fba_only + fba_inbound + fba_fc_transfers + fba_fc_processing
                     + local_stock + purchase_on_order)
        daily_sales = lx_daily
        # 护栏：领星预估日销<=0（无销量）时，领星可售天数可能是"库存/0"的失真值
        # （如 21111/16711/13530 天），不能直接采信；改用实际销量兜底，仍无销量则不判积压
        low_sales = lx_daily <= 0
        if lx_days > 0 and lx_daily > 0:
            inventory_days = lx_days
            days_formula = (f"领星(数据过少兜底)：领星可售天数{lx_days}"
                            f"（领星预估日销{lx_daily}）")
        elif low_sales:
            # 无领星日销：用系统实际销量兜底（近3天 > 30天均量 > 30天/30），避免把零销量当积压
            fallback_daily = recent_3_days_avg or average_thirty_volume or (thirty_volume / 30 if thirty_volume > 0 else 0)
            inventory_days = round(available / fallback_daily) if fallback_daily > 0 else None
            if inventory_days is None:
                days_formula = f"领星(数据过少兜底)：领星预估日销为0且无系统销量兜底，覆盖天数不计算（兜底原因：{lx_fallback_reason}）"
            else:
                days_formula = (f"领星(数据过少兜底)：领星预估日销为0→改用系统销量{round(fallback_daily, 2)}，"
                                f"可用库存{available}÷{round(fallback_daily, 2)}≈{inventory_days}天（兜底原因：{lx_fallback_reason}）")
        elif daily_sales > 0:
            inventory_days = round(available / daily_sales)
            days_formula = (f"领星(数据过少兜底)：可用库存{available}÷领星预估日销{daily_sales}"
                            f"≈{inventory_days}天（兜底原因：{lx_fallback_reason}）")
        else:
            inventory_days = None  # 无销量且领星无数据：不再兜底999
            days_formula = f"领星(数据过少兜底)：无销量数据，覆盖天数不计算（兜底原因：{lx_fallback_reason}）"
        # 库存天数上限：防止失真大值（如21111天）污染积压提醒，超1年按365天展示
        if inventory_days is not None:
            _raw_days = inventory_days
            inventory_days = min(inventory_days, 365)
            if _raw_days > inventory_days:
                days_formula += f"；超365天上限截断为{inventory_days}天"
        stock_formula = (f"FBA可售{fba_only}+FBA在途{fba_inbound}"
                         f"+待调仓{fba_fc_transfers}+调仓中{fba_fc_processing}"
                         f"+本地库存{local_stock}+采购待到货{purchase_on_order}={available}（领星口径不含FBA预留）")
        stockout_date = _pick(
            prod.stockout_date if prod else None,
            snap.stockout_date if snap else None,
            default=None,
        )
        lx_used = True
        recent_3_days_avg = lx_daily  # 触发判断以领星预估日销为基准

    # 修复2：在途库存预计上架时间点（FBA在途 + 采购待到货 何时能上架售卖）
    # 优先使用物流追踪实时库的真实到货时间（arrival_date = 预计到港 + 14天）；
    # 接口无数据/失败时回退估算：今天 + 到货天数（海运 淡季30/旺季45，当前8月起为旺季45） + FBA入仓上架缓冲3天。
    # 口径说明：在途合计 = 仅 FBA 在途（不含"采购待到货"；采购待到货已在可用库存中单列）。
    inbound_total = fba_inbound
    # 上架日判定口径：FBA在途 + 采购待到货（两者任一 >0 均需估算到货/上架时间）
    _inbound_for_arrival = fba_inbound + purchase_on_order
    inbound_arrival_date = None
    inbound_arrival_source = None            # 物流实时库 / 估算公式
    inbound_arrival_formula = "无在途（FBA在途+采购待到货=0），不计算上架日"
    if _inbound_for_arrival > 0:
        _lx_err = ""
        try:
            from app.services.logistics_arrival import get_inbound_arrival_date, get_last_error
            inbound_arrival_date = await get_inbound_arrival_date(asin)
            # 物流服务内部对请求异常做了兜底（返回空映射），这里取回真实原因，
            # 以便判断依据能区分"接口异常"与"接口正常但无该ASIN在途记录"
            _lx_err = (get_last_error() or "")[:80]
        except Exception as _e:  # noqa: BLE001
            _lx_err = str(_e)[:80]
            logger.warning(f"[{asin}] 获取物流实时到货时间失败: {_e}")
        if inbound_arrival_date:
            inbound_arrival_source = "物流实时库"
            inbound_arrival_formula = (
                f"物流实时库真实到货时间（该库口径=预计到港+14天）：{inbound_arrival_date}"
            )
        else:
            _is_peak = 8 <= today.month <= 12
            _sea_days = settings.SEA_PEAK_DAYS if _is_peak else settings.SEA_SLOW_DAYS
            inbound_arrival_date = (today + timedelta(days=_sea_days + 3)).isoformat()
            inbound_arrival_source = "估算公式"
            _missing = (f"物流实时库接口异常：{_lx_err}" if _lx_err
                        else f"物流实时库未返回{asin}的在途记录")
            inbound_arrival_formula = (
                f"估算公式：今天{today.isoformat()} + 海运{'旺季' if _is_peak else '淡季'}{_sea_days}天"
                f" + 上架缓冲3天 = {inbound_arrival_date}（{_missing}）"
            )

    return {
        "available_stock": available,
        "fba_available": fba_only,
        "inventory_days": inventory_days,
        "replenishment_cycle": 30,
        "urgency_score": 0,  # 在评分里计算
        "thirty_volume": thirty_volume,
        "average_thirty_volume": average_thirty_volume,
        "last_year_thirty_volume": last_year_thirty_volume,
        "recent_3_days_avg": recent_3_days_avg,
        "fba_reserved": fba_reserved,
        "fba_inbound": fba_inbound,
        "local_stock": local_stock,
        "purchase_on_order": purchase_on_order,
        "inbound_total": inbound_total,
        "inbound_arrival_date": inbound_arrival_date,  # 在途库存预计上架时间点
        # 口径溯源（步骤6判断依据展示，区分系统公式/领星兜底/物流库/估算）
        "inventory_days_formula": days_formula,
        "available_stock_formula": stock_formula,
        "inbound_arrival_source": inbound_arrival_source,
        "inbound_arrival_formula": inbound_arrival_formula,
        # 领星口径（交叉验证/兜底）：FBA可售天数/预计售罄日/预估日销
        "fba_available_days": lx_days_raw,
        "stockout_date": stockout_date,
        "estimated_daily_sales": lx_daily_raw,
        "lx_used": lx_used,
        "data_days": data_days,
    }


def _normalize_verdict(level) -> str | None:
    """等级 → 三裁判归一化意见：采购 / 观察 / 暂停"""
    if not level:
        return None
    level = str(level).strip()
    if level in ("立即采购", "建议采购", "需要采购", "加订"):
        return "采购"
    if level == "观察":
        return "观察"
    if level in ("暂停", "终止", "未触发", "禁止采购", "终止加订"):
        return "暂停"
    return None


def _arbitrate_verdicts(rule, special, ai) -> tuple[str, str, list]:
    """三裁判仲裁：两票一致即定；三方都不一致 → 规则裁判为准。

    返回 (最终意见, 依据说明, [("裁判", 意见), ...])
    """
    votes = []
    if rule:
        votes.append(("规则裁判", rule))
    if special:
        votes.append(("特例裁判", special))
    if ai:
        votes.append(("AI裁判", ai))
    if not votes:
        return "暂停", "无有效裁判意见（规则/特例/AI 均无判定）", votes
    if len(votes) == 1:
        return votes[0][1], f"仅{votes[0][0]}给出意见「{votes[0][1]}」", votes
    from collections import Counter

    cnt = Counter(v for _j, v in votes)
    verdict, n = cnt.most_common(1)[0]
    if n >= 2:
        agree = [j for j, v in votes if v == verdict]
        return verdict, f"三裁判仲裁：{'、'.join(agree)}一致判定「{verdict}」", votes
    # 三方都不一致 → 规则裁判为准
    if rule:
        return rule, f"三裁判意见各异（{dict(cnt)}），按规则裁判为准判定「{rule}」", votes
    # 规则裁判无意见时按 特例 > AI 的既有顺序取首个
    return votes[0][1], f"三裁判意见各异且规则裁判无意见，按「{votes[0][0]}」判定「{votes[0][1]}」", votes


async def _check_second_peak_replenishment(
    product,
    inventory: dict,
    suggested_qty: int,
    lead_time: int,
    purchase_window: dict,
    life_cycle: str,
    calc_date: date,
    session,
) -> dict:
    """第二高峰期补货判断（最后需要补货时）

    用户规则：最后需要补货（成熟期触发需要采购）时——
      补货后库存能临近第二个高峰期 → 允许提前补货；
      否则即使在成熟期也不补货。

    第二个高峰期来源（按优先级）：
      1) 节日销售时间段（festival_periods）中当前目标段之后的下一段；
      2) 核心热卖月（core_months）中当前月起的未来峰值月里最靠后的峰值月
         （单峰值月 → 次年同月，视为远不可及）。

    返回: {"applicable": bool, "allowed": bool, "second_peak_start": date|None,
           "coverage_until": date|None, "detail": str}
    """
    today = calc_date or date.today()

    # ── 1) 定位第二个高峰期 ──
    second_peak_start = None
    source = ""
    festival = getattr(product, "festival", None)
    if festival:
        try:
            from app.services.time_axis import get_festival_info, _target_festival_date

            info = await get_festival_info(festival, session)
            if info:
                periods = info.get("festival_periods") or []
                target_start, _in_period = _target_festival_date(info, today)
                later = sorted(
                    p["start"] for p in periods
                    if p.get("start") and (target_start is None or p["start"] > target_start)
                )
                if later:
                    second_peak_start = later[0]
                    source = f"节日[{festival}]后续销售段"
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[{getattr(product, 'asin', '')}] 节日高峰期解析失败: {e}")

    if second_peak_start is None:
        cm = str(getattr(product, "core_months", "") or "").replace("，", ",")
        months = sorted({int(m) for m in cm.split(",") if m.strip().isdigit()})
        if months:
            m0 = today.month
            upcoming = [m for m in months if m >= m0]
            if not upcoming:
                upcoming = [m + 12 for m in months]
            if len(set(upcoming)) >= 2:
                # 多峰值月（如 10,11,12）：取本周期最靠后的峰值月作为第二个高峰期
                nxt = max(upcoming)
                y = today.year + (nxt // 12 if nxt > 12 else 0)
                second_peak_start = date(y, nxt % 12 or 12, 1)
                source = f"核心热卖月[{cm}]后续峰值月"
            else:
                # 单峰值月：次年同月（跨周期，通常无法临近）
                second_peak_start = date(today.year + 1, upcoming[0] % 12 or 12, 1)
                source = f"核心热卖月[{cm}]下一周期峰值月"

    if second_peak_start is None:
        return {
            "applicable": True, "allowed": False,
            "second_peak_start": None, "coverage_until": None,
            "detail": "当前之后无第二个高峰期（无节日后续销售段/核心热卖月），最后阶段不补货（即使在成熟期）",
        }

    # ── 2) 补货后覆盖能力（与触发判断同一日销口径） ──
    daily = 0.0
    r3 = float(inventory.get("recent_3_days_avg") or 0)
    t30 = float(inventory.get("thirty_volume") or 0) / 30.0
    a30 = float(inventory.get("average_thirty_volume") or 0)
    if r3 > 0:
        daily = r3
    elif t30 > 0:
        daily = t30
    elif a30 > 0:
        daily = a30
    if daily <= 0:
        lx_daily = float(inventory.get("estimated_daily_sales") or 0)
        if lx_daily > 0:
            daily = lx_daily
    if daily <= 0:
        return {
            "applicable": True, "allowed": False,
            "second_peak_start": second_peak_start, "coverage_until": None,
            "detail": f"无销量数据（日销为0），无法评估补货后能否临近第二个高峰期（{second_peak_start}），最后阶段不补货",
        }
    daily *= new_product_policy.LIFECYCLE_FACTORS.get(life_cycle, (1.0, 1.2))[0]

    transport_days = {
        "海运": settings.SEA_PEAK_DAYS,
        "空派": settings.AIR_PEAK_DAYS,
        "快递": settings.EXPRESS_PEAK_DAYS,
    }.get((purchase_window or {}).get("recommended_transport") or "", settings.SEA_PEAK_DAYS)
    arrival_days = int(lead_time or 30) + transport_days
    total_stock_after = (
        int(inventory.get("available_stock") or 0)
        + int(inventory.get("local_stock") or 0)
        + int(inventory.get("purchase_on_order") or 0)
        + int(suggested_qty or 0)
    )
    coverage_days = round(total_stock_after / daily)
    coverage_until = today + timedelta(days=arrival_days + coverage_days)

    approach_days = SECOND_PEAK_APPROACH_DAYS
    try:
        from app.services.config_service import get_param

        approach_days = int(await get_param(session, "second_peak_approach_days") or SECOND_PEAK_APPROACH_DAYS)
    except Exception:  # noqa: BLE001
        pass

    reach = coverage_until >= second_peak_start - timedelta(days=approach_days)
    gap = (second_peak_start - timedelta(days=approach_days) - coverage_until).days
    detail = (
        f"补货后库存覆盖至{coverage_until}（到货{arrival_days}天+可售{coverage_days}天），"
        f"第二个高峰期[{source}]为{second_peak_start}"
        + ("，可临近，允许提前补货" if reach else f"，无法临近（还差{gap}天），最后阶段不补货（即使在成熟期）")
    )
    return {
        "applicable": True,
        "allowed": reach,
        "second_peak_start": second_peak_start,
        "coverage_until": coverage_until,
        "detail": detail,
    }


def _check_hard_rules(product: Product, inventory: dict, cfg: dict = None) -> Optional[dict]:
    """硬规则拦截（级别高于三裁判 / 新品门禁；新老品统一口径）

    ① 成本表三渠道 Profit 全为负 → 停止加购
    ② 剩余售卖天数（库存可售天数 inventory_days）< 14 天 → 直接终止加订

    命中返回 {"rule": ..., "detail": ...}，未命中返回 None。
    """
    try:
        cost_table = (
            new_product_policy.calc_cost_table(product, cfg=cfg)
            if cfg else new_product_policy.calc_cost_table(product)
        )
        if not cost_table.get("all_profitable"):
            profits = {m: (c or {}).get("profit") for m, c in (cost_table.get("channels") or {}).items()}
            return {
                "rule": "成本表利润全负停止加购",
                "detail": f"成本表三渠道 Profit 均为负（{profits}），停止加购",
            }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[{getattr(product, 'asin', '')}] 成本表硬规则计算失败，跳过盈利门槛: {e}")
    remain_days = inventory.get("inventory_days")
    if remain_days is not None and int(remain_days) < 14:
        return {
            "rule": "剩余售卖天数不足终止加订",
            "detail": f"剩余售卖天数（库存可售天数）={int(remain_days)}天 < 14天，直接终止加订",
        }
    return None


def _check_long_term_stock_cover(forecast: dict, inventory: dict, product: Product) -> Optional[dict]:
    """长期产品库存充足拦截（新老品统一口径，级别高于评分>80/特例加订/三裁判/AI）

    长期产品若「预测未来销量 ≤ 可用库存」，说明当前可用库存已覆盖未来预测销量，
    即使采购评分 > 80 也不采购。

    命中返回 {"rule", "detail", ...}，未命中（非长期产品 / 预测未来销量>可用库存）返回 None。
    """
    if (getattr(product, "product_type", "") or "").strip() != "长期产品":
        return None
    forecast_total = forecast.get("forecast_total")
    if forecast_total is None:
        months = forecast.get("forecast_months") or []
        forecast_total = sum(float(m.get("forecast_qty") or 0) for m in months)
    forecast_total = float(forecast_total or 0)
    available_stock = float(inventory.get("available_stock") or 0)
    if forecast_total > available_stock:
        return None
    return {
        "rule": "长期产品库存充足不采购",
        "forecast_total": forecast_total,
        "available_stock": available_stock,
        "detail": (
            f"长期产品预测未来销量{forecast_total} ≤ 可用库存{available_stock}，"
            f"库存已覆盖未来需求，不采购（即使评分>80）"
        ),
    }


async def _run_new_product_flow(
    asin: str,
    product: Product,
    session: AsyncSession,
    recorder: StepRecorder,
    calc_date: date,
    inventory: dict,
    life_cycle: str,
    lead_time: int,
    purchase_window: dict = None,
) -> dict:
    """新品补货决策流程（需求文档第六章，新品只走本流程）

    硬规则拦截：命中即终止加订（级别高于新品门禁，口径与老品一致）。
    过季拦截：季节/节日产品若销售时间段已全部结束（无法赶上销售窗口），
    直接终止不加订（如高峰期4-5月已过、下次备货窗口遥远的产品）。
    """
    # ── 硬规则拦截（级别最高，与老品同一口径）：命中即终止加订 ──
    #    ① 成本表三渠道 Profit 全为负 → 停止加购
    #    ② 剩余售卖天数（库存可售天数）< 14 天 → 直接终止加订
    hard_rule_hit = _check_hard_rules(product, inventory)
    if hard_rule_hit:
        reason = hard_rule_hit["detail"]
        logger.info(f"[{asin}] 新品硬规则拦截: {reason}")
        recorder.record(101, "硬规则拦截（级别最高）", {
            "rule": hard_rule_hit["rule"],
            "inventory_days": inventory.get("inventory_days"),
        }, input_data={"asin": asin, "inventory_days": inventory.get("inventory_days")},
           reason=reason, status="failed")
        return {
            "trigger": {
                "purchase_trigger": "终止加订",
                "reason": reason,
                "inventory_days": inventory.get("inventory_days", 0),
                "replenishment_cycle": inventory.get("replenishment_cycle", 30),
            },
            "suggested_qty": 0,
            "batch_plan": {"batches": [], "total_qty": 0},
            "scoring": {
                "purchase_score": None,
                "purchase_level": "终止",
                "score_detail": {
                    "level": "终止", "suggested_qty": 0, "reason": reason,
                    "硬规则拦截": hard_rule_hit,
                },
            },
        }
    # ── 过季拦截：无法赶上销售窗口 → 不加订（长期产品不过季，由 time_axis 返回可采购） ──
    if purchase_window and not purchase_window.get("can_purchase", True):
        reason = f"过季/无法赶上销售窗口：{purchase_window.get('reason', '')} | 不加订"
        logger.info(f"[{asin}] 新品过季拦截: {reason}")
        recorder.record(101, "销售时间轴判断", {
            "phase": None,
            "can_purchase": False,
            "reason": reason,
        }, input_data={"asin": asin, "festival": product.festival},
           reason=reason, status="failed")
        trigger = {
            "purchase_trigger": "终止加订",
            "reason": reason,
            "inventory_days": inventory.get("inventory_days", 0),
            "replenishment_cycle": inventory.get("replenishment_cycle", 30),
        }
        return {
            "trigger": trigger,
            "suggested_qty": 0,
            "batch_plan": {"batches": [], "total_qty": 0},
            "scoring": {"purchase_score": None, "purchase_level": "终止", "score_detail": {
                "level": "终止", "suggested_qty": 0, "reason": reason,
                "过季拦截": {"detail": reason},
            }},
        }

    cfg = await _get_new_product_cfg(session)
    trigger_days = cfg["trigger_days"]

    # 触发条件所需：最近单量
    daily_orders = await _recent_daily_orders(asin, session, days=max(trigger_days, 7))
    recent = daily_orders[-trigger_days:] if daily_orders else []
    base_daily = sum(recent) / max(len(recent), 1)

    # ACOS（产品档案）
    acos = await _fetch_acos(product)

    # 节日日期
    festival_date = None
    if product.festival:
        info = await get_festival_info(product.festival, session)
        festival_date = info.get("festival_date") if info else None

    # 启动期起始日（节日生命周期时间点表「启动期」区间起始日）：启动期指数 系数^(经过天数÷10) 的起算点
    launch_start = None
    if festival_date and (getattr(product, "product_type", "") or "").strip() != "长期产品":
        from app.services.festival_lifecycle import launch_stage_start

        launch_start = await launch_stage_start(product, session, calc_date)

    # 缓存天数判定依据：AI 语义分类（装饰品/非装饰品）
    is_decoration = (await get_semantic_classification(session, product.asin) or "").strip() == "装饰品"
    decision = new_product_policy.new_product_decision(
        product=product,
        daily_orders=daily_orders,
        acos=acos,
        available_stock=int(inventory.get("available_stock", 0) or 0),
        base_daily_sales=base_daily,
        life_cycle=life_cycle,
        festival_date=festival_date,
        is_decoration=is_decoration,
        lead_time=lead_time,
        cfg=cfg,
        current_date=calc_date,
        launch_start=launch_start,
    )

    # 记录门禁步骤（新品独立编号段：101 起，前端显示 N1/N2…，与老品 1-12 不冲突）
    step_no = 101
    for s in decision.get("steps", []):
        recorder.record(
            step_no,
            s.get("name", "新品决策"),
            s.get("data", {}),
            reason=s.get("reason", ""),
            status={"pass": "success", "skip": "skip"}.get(s.get("status"), "failed"),
            input_data={
                "asin": asin,
                "product_stage": product.product_stage,
                "festival": product.festival,
                "life_cycle": life_cycle,
                "lead_time": lead_time,
                "available_stock": inventory.get("available_stock"),
                "acos": acos,
                "daily_orders": daily_orders[-trigger_days:],
            },
        )
        step_no += 1

    level = decision.get("level", "未触发")
    trigger_text = {"建议采购": "需要采购", "终止": "终止加订", "未触发": "未触发"}.get(level, "未触发")
    suggested_qty = int(decision.get("suggested_qty", 0) or 0)

    # 批次规划（复用老品批次逻辑）
    batch_plan = _plan_batches(suggested_qty, product, lead_time)
    recorder.record(step_no, "采购批次规划", batch_plan,
                    reason=f"拆分为{len(batch_plan.get('batches', []))}批次",
                    input_data={"suggested_qty": suggested_qty})
    step_no += 1
    recorder.record(step_no, "新品补货决策完成",
                    {"level": level, "suggested_qty": suggested_qty, "reason": decision.get("reason", "")},
                    reason=decision.get("reason", ""),
                    input_data={"decision": decision})

    # 回填库存天数/补货周期（展示用）
    plan = decision.get("plan") or {}
    if plan.get("sellout_days") is not None:
        inventory["inventory_days"] = int(plan["sellout_days"])
    if plan.get("replenishment_cycle"):
        inventory["replenishment_cycle"] = plan["replenishment_cycle"]

    trigger = {
        "purchase_trigger": trigger_text,
        "reason": decision.get("reason", ""),
        "inventory_days": inventory.get("inventory_days", 0),
        "replenishment_cycle": inventory.get("replenishment_cycle", 30),
    }
    scoring = {
        "purchase_score": None,
        "purchase_level": level,
        "score_detail": decision,
    }
    return {"trigger": trigger, "suggested_qty": suggested_qty, "batch_plan": batch_plan, "scoring": scoring}


def _festival_window_months(rec) -> list[int]:
    """从节日日历生命周期的五个阶段中提取「完整生命周期」窗口月份集合（并集，按月取整）。

    示例（秋季类）：启动期=8月、增长期=9月-10月中旬、热卖期=10月下旬-11月上旬、
    成熟期=11月中旬、下降期=11月下旬 → [8, 9, 10, 11]。
    支持跨年区间（如 冬季类 12月-1月）区间内部自动向后封装月份。
    解析复用 festival_lifecycle.parse_stage，与生命周期判定保持同一口径。
    """
    from app.services.festival_lifecycle import parse_stage, LIFECYCLE_STAGES, STAGE_FIELDS

    months: set[int] = set()
    for stage in LIFECYCLE_STAGES:
        raw = getattr(rec, STAGE_FIELDS[stage], None) if rec is not None else None
        for start_m, _sd, end_m, _ed in parse_stage(raw):
            m = start_m
            while True:
                months.add(m)
                if m == end_m:
                    break
                m = m % 12 + 1  # 跨年区间向后封装（12 → 1 → 2）
    return sorted(months)


def _window_coverage(inventory: dict, window: dict, win_start: date, win_end: date) -> dict:
    """窗口库存覆盖天数（老品库存天数的唯一口径）

    各月预估 = 去年窗口该月逐日求和 × 趋势系数；该月日均 = 该月预估 ÷ 该月天数
      （首月为「明天→月末」的不完整月，天数与去年同段一致）；
    自窗口首日（明天）起逐月消耗可用库存，耗尽月按「余额 ÷ 该月日均」折算天数，
    即「看可用库存坐落在哪个年月，得出覆盖天数与覆盖截止日」。
    去年同窗口无任何销量（无各月占比）→ days 返回 None，库存天数留空。
    """
    if not (window.get("monthly_sales") or []):
        return {"days": None, "until": None, "months": [], "demand_total": 0}
    available = float(inventory.get("available_stock") or 0)
    trend = float(window.get("trend_coeff") or 1.0)
    rows: list[dict] = []
    covered = 0.0
    until: date | None = None
    for item in window.get("monthly_sales") or []:
        _y, _, _m = (item.get("month") or "").partition("-")
        if not _y or not _m:
            continue
        y, m = int(_y) + 1, int(_m)  # 去年窗口月份 → 今年对应月份
        m_start = max(date(y, m, 1), win_start)
        m_end = min(_month_end(date(y, m, 1)), win_end)
        if m_end < m_start:
            continue
        span = (m_end - m_start).days + 1
        demand = float(item.get("qty") or 0) * trend
        daily = demand / span
        if daily <= 0 or available >= demand:
            rows.append({"月": f"{y}-{m:02d}", "该月天数": span, "该月预估": round(demand),
                         "该月日均": round(daily, 1), "本月消耗": round(demand),
                         "月末剩余": round(available - demand)})
            available -= demand
            covered += span
            continue
        part = available / daily
        rows.append({"月": f"{y}-{m:02d}", "该月天数": span, "该月预估": round(demand),
                     "该月日均": round(daily, 1), "本月消耗": round(available), "月末剩余": 0,
                     "覆盖到": (m_start + timedelta(days=int(part) - 1)).isoformat() if part >= 1 else None})
        covered += part
        if part >= 1:
            until = m_start + timedelta(days=int(part) - 1)
        available = 0
        break
    days = min(round(covered), 365)
    if until is None and days > 0:
        until = win_start + timedelta(days=days - 1)
    return {
        "days": days,
        "until": until.isoformat() if until else None,
        "months": rows,
        "demand_total": round(sum(r["该月预估"] for r in rows)),
    }


def _old_product_inventory_days(inventory: dict, festival_window: dict | None = None) -> dict:
    """老品库存天数唯一口径：窗口「各月预估 ÷ 该月天数 → 按库存坐落月逐月扣减」

    数据源 = _window_coverage（节日窗口优先，其次长期产品窗口）：
      - 有各月占比 → days = 覆盖天数（上限365），formula 写明各月日均与覆盖截止日；
      - 去年同窗口无销量（无各月占比）→ days = None（库存天数留空）；
      - 无任何窗口（老品无节日且非长期）→ days = None 且 source = "none"
        （库存天数留空、不触发采购；数据过少的产品走领星兜底，不在本函数内）。

    返回 {"days": int|None, "source": "window"|"none", "formula": str}
    """
    cov_days = None
    cov_months: list = []
    cov_until = None
    demand_total = 0
    src = ""
    window_label = ""
    if festival_window:
        src = "节日窗口"
        cov_days = festival_window.get("coverage_days")
        cov_months = festival_window.get("coverage_months") or []
        cov_until = festival_window.get("coverage_until")
        demand_total = festival_window.get("coverage_demand_total") or 0
        _wm = festival_window.get("window_months") or []
        window_label = (f"{_wm[0]}~{_wm[-1]}月" if _wm
                        else f"{festival_window.get('win_start')}~{festival_window.get('win_end')}")
    else:
        lt = inventory.get("long_term_window") or {}
        if lt:
            src = "长期窗口"
            cov_days = lt.get("coverage_days")
            cov_months = lt.get("coverage_months") or []
            cov_until = lt.get("coverage_until")
            demand_total = lt.get("coverage_demand_total") or 0
            window_label = f"{lt.get('window_start')}~{lt.get('window_end')}"
    if not src:
        return {"days": None, "source": "none",
                "formula": "无窗口（无节日且非长期）→ 库存天数留空、不触发采购"}
    if cov_days is None:
        return {"days": None, "source": "window",
                "formula": f"{src}[{window_label}]去年同窗口无销量（无各月占比）→ 库存天数不计算（留空）"}
    days = min(int(cov_days), 365)
    daily_desc = "、".join(
        f"{str(r.get('月'))[5:]}月{r.get('该月日均')}件/天" for r in cov_months
    ) or "(窗口口径)"
    return {
        "days": days,
        "source": "window",
        "formula": (f"{src}[{window_label}]预估总需求{demand_total}，各月日均{daily_desc}，"
                    f"自窗口首日起逐月扣减可用库存，可售至{cov_until or '窗口内'}共{days}天"),
    }


async def _calc_festival_window(product: Product, session: AsyncSession, inventory: dict) -> dict | None:
    """老品节日产品：节日窗口预估（与 Step 4/5 统一口径，Q4）

    新口径（用户确认）：
      1. 今年窗口 = [明天, 今年 festival_calendar.festival_end]，去年同期 = [去年明天, 去年 festival_end]；
      2. 基数 = 去年窗口各月**逐日求和**（首尾月为不完整月）；
      3. 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0），上限 1.5（超过直接用 1.5）；
      4. 今年窗口总需求 = 基数 × 趋势系数；各月需求 = 去年该月 × 趋势系数；
      5. 剩余需求 = 窗口总需求 − 窗口内已售 − 可用库存 − 本地仓 − 采购在途。

    无法套用新口径（无节日 / 长期产品 / 未匹配 festival_calendar / festival_end 缺失或已早于
    「明天」，后者本轮不处理、由人工修订表数据）时，退回旧口径实现，保证这部分产品行为不变。
    """
    fw = await _festival_sales_window(product, session)
    if not fw:
        return await _calc_festival_window_legacy(product, session, inventory)

    today = date.today()
    window_months = fw["window_months"]
    win_start = fw["window_start"]
    win_end = fw["window_end"]
    trend = fw["trend_coeff"]
    window_estimate = round(fw["base_total"] * trend)
    last_year_by_month = {item["month"]: item["qty"] for item in fw["monthly_sales"]}

    # 窗口天数 = 今年连续日期区间（明天 → 节日结束）的实际天数
    window_days = max((win_end - win_start).days + 1, 1)

    available_stock = int(inventory.get("available_stock") or 0)
    remaining = max(0, window_estimate - available_stock)

    # 未来窗口各月预估需求（用于分批次保护热卖月）：去年该月 × 趋势系数
    month_estimate = {}
    for item in fw["monthly_sales"]:
        _y, _, _m = (item.get("month") or "").partition("-")
        if not _y or not _m:
            continue
        month_estimate[int(_m)] = round((item.get("qty") or 0) * trend)

    future_months = [m for m in window_months if m >= today.month] or list(window_months)

    # 库存覆盖天数：按各月预估逐月扣减可用库存（库存坐落在哪个月 → 覆盖到哪一天）
    coverage = _window_coverage(inventory, fw, win_start, win_end)

    return {
        "window_months": window_months,
        "festival_year": fw["festival_year"],
        "baseline": fw["base_total"],
        "last_year_by_month": last_year_by_month,
        "this_year_30d": fw["recent_30d_qty"],
        "last_year_30d": fw["last_year_30d_qty"],
        "g": round(trend - 1.0, 4),
        "g_raw": round(fw["trend_coeff_raw"] - 1.0, 4),
        "trend_coeff": trend,
        "trend_coeff_raw": fw["trend_coeff_raw"],
        "window_estimate": window_estimate,
        "window_days": window_days,
        "remaining": remaining,
        "win_start": win_start,
        "win_end": win_end,
        "month_estimate": month_estimate,
        "future_months": future_months,
        "hot_end_month": fw["hot_end_month"],
        # 库存覆盖（逐月扣减口径）
        "coverage_days": coverage["days"],
        "coverage_until": coverage["until"],
        "coverage_months": coverage["months"],
        "coverage_demand_total": coverage["demand_total"],
    }


async def _calc_festival_window_legacy(product: Product, session: AsyncSession, inventory: dict) -> dict | None:
    """（旧口径，兜底）老品节日产品：窗口同期增长趋势预估（T1-1~5）

    设计（用户确认口径）：
      1. 「窗口」= 节日完整生命周期（festival_calendar 启动期~下降期覆盖的月份），
         非仅热卖期月份；
      2. G（同期销量增长趋势）= (今年近30天销量 − 去年同30天销量) / 去年同30天销量，
         数据源 = SalesData 日明细（今年 vs 去年同一日历段）；
      3. 窗口基准 = 去年窗口各月 HistoricalMonthlyStats(source=lingxing) 销量求和；
      4. 今年窗口总销量 = 窗口基准 × (1 + G)；
      5. 剩余需求 = 窗口总销量 − 窗口内已售 − 可用库存 − 本地仓 − 采购在途。

    边界：去年同30天销量为 0（SalesData 无去年数据）→ G 取 0，即不放大、按窗口基准原值预估。

    返回 dict（含窗口月份、窗口天数、基准、G、窗口预估、已售、剩余、各月预估、热卖月），失败返回 None。
    """
    from app.services.festival_lifecycle import match_festival_name
    from app.models.festival_calendar import FestivalCalendar
    from app.models.historical_monthly import HistoricalMonthlyStats

    festival = getattr(product, "festival", None)
    if not festival:
        return None
    # 长期产品全年销售不过季，不参与节日窗口预估
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return None
    name = await match_festival_name(festival, session)
    if not name:
        return None
    recs = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    if not recs:
        return None

    rec = recs[0]
    if len(recs) > 1:
        today = date.today()
        # 选 festival_date/结束日距当前最近的一条（与 get_festival_info 口径一致）
        best, best_gap = recs[0], None
        for r in recs:
            target = r.festival_date or r.listing_start or r.festival_end
            if target is None:
                continue
            gap = abs((target.date() - today).days)
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap
        rec = best

    window_months = _festival_window_months(rec)
    if not window_months:
        return None

    # 目标节日年份：festival_date > listing_start > 当前年
    if rec.festival_date:
        festival_year = rec.festival_date.year
    elif rec.listing_start:
        festival_year = rec.listing_start.year
    else:
        festival_year = date.today().year
    baseline_year = festival_year - 1  # 去年窗口

    # ── 去年窗口各月销量求和（窗口基准） ──
    month_keys = [f"{baseline_year}-{m:02d}" for m in window_months]
    hist_rows = (await session.execute(
        select(HistoricalMonthlyStats).where(
            HistoricalMonthlyStats.asin == product.asin,
            HistoricalMonthlyStats.month.in_(month_keys),
            HistoricalMonthlyStats.source == "lingxing",
        )
    )).scalars().all()
    last_year_by_month = {r.month: (r.sale_quantity or 0) for r in hist_rows}
    baseline = sum(last_year_by_month.values())

    # ── 今年/去年 近30天销量（SalesData 日明细） ──
    today = date.today()
    this_start = today - timedelta(days=29)
    last_start = this_start - timedelta(days=365)
    last_end = today - timedelta(days=365)
    this_year_30d = int((await session.execute(
        select(func.coalesce(func.sum(SalesData.sales_qty), 0))
        .where(SalesData.asin == product.asin,
               SalesData.date >= this_start, SalesData.date <= today)
    )).scalar_one() or 0)
    last_year_30d = int((await session.execute(
        select(func.coalesce(func.sum(SalesData.sales_qty), 0))
        .where(SalesData.asin == product.asin,
               SalesData.date >= last_start, SalesData.date <= last_end)
    )).scalar_one() or 0)
    # 双源优先：daily_sales_stats（逐日实抓，含去年数据）优先，缺时回退 sales_data
    if last_year_30d == 0:
        fb_ly = await sum_daily_sales_dual(product.asin, last_start, last_end, session)
        if fb_ly:
            last_year_30d = fb_ly
    if this_year_30d == 0:
        fb_ty = await sum_daily_sales_dual(product.asin, this_start, today, session)
        if fb_ty:
            this_year_30d = fb_ty

    # G：去年同30天为 0（SalesData 无历史）→ 不放大，按基准原值
    if last_year_30d > 0:
        g = (this_year_30d - last_year_30d) / last_year_30d
    else:
        g = 0.0
    # 最终趋势系数口径：上限 1.5（超过直接用 1.5；原值 g 另存便于文字表述）
    trend_coeff_raw = 1 + g
    trend_coeff = round(min(trend_coeff_raw, TREND_COEFF_MAX), 4)
    g_capped = round(trend_coeff - 1.0, 4)

    # ── 今年窗口总销量 = 窗口基准 × 趋势系数 ──
    window_estimate = round(baseline * trend_coeff)

    # ── 窗口内已售：今年窗口起始（首个月1号）→ 窗口结束（末个月月末），不超出今天 ──
    win_start = date(festival_year, min(window_months), 1)
    max_m = max(window_months)
    win_end = (date(festival_year, max_m, 31) if max_m == 12
               else date(festival_year, max_m + 1, 1) - timedelta(days=1))
    # ── 窗口天数 = 窗口集合内各月实际天数之和（不用 win_start→win_end 跨度） ──
    #    窗口月份跨年/跳跃时（如 [1,2,3,12]）跨度会覆盖全年 365 天，把不销售的月份
    #    也计入分母，等于把日均"年化"，会高估可售天数、低估断货风险。
    window_days = 0
    for _m in window_months:
        _ms = date(festival_year, _m, 1)
        _me = date(festival_year + 1, 1, 1) if _m == 12 else date(festival_year, _m + 1, 1)
        window_days += (_me - _ms).days
    window_days = max(window_days, 1)

    # ── 剩余需求 = 窗口预估值 − 可用库存 ──
    available_stock = int(inventory.get("available_stock") or 0)
    remaining = max(0, window_estimate - available_stock)

    # ── 未来窗口各月预估需求（用于分批次保护热卖月） ──
    month_estimate = {}
    for m in window_months:
        bm = last_year_by_month.get(f"{baseline_year}-{m:02d}", 0)
        month_estimate[m] = round(bm * trend_coeff)
    future_months = [m for m in window_months if m >= today.month] or list(window_months)
    hot_end_month = int(rec.hot_end_month or max(window_months))

    # 库存覆盖天数：与节日主口径同源（去年各月 × 趋势系数 → 按各月天数逐月扣减可用库存）
    coverage = _window_coverage(
        inventory,
        {"monthly_sales": [{"month": k, "qty": v} for k, v in sorted(last_year_by_month.items())],
         "trend_coeff": trend_coeff},
        win_start, win_end,
    )

    return {
        "window_months": window_months,
        "festival_year": festival_year,
        "baseline": baseline,
        "last_year_by_month": last_year_by_month,
        "this_year_30d": this_year_30d,
        "last_year_30d": last_year_30d,
        "g": g_capped,
        "g_raw": round(g, 4),
        "trend_coeff": trend_coeff,
        "trend_coeff_raw": round(trend_coeff_raw, 4),
        "window_estimate": window_estimate,
        "window_days": window_days,
        "remaining": remaining,
        "win_start": win_start,
        "win_end": win_end,
        "month_estimate": month_estimate,
        "future_months": future_months,
        "hot_end_month": hot_end_month,
        # 库存覆盖（逐月扣减口径）
        "coverage_days": coverage["days"],
        "coverage_until": coverage["until"],
        "coverage_months": coverage["months"],
        "coverage_demand_total": coverage["demand_total"],
    }


