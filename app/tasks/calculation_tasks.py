"""批量计算调度模块 - 计算编排器（支持按等级频率计算）"""

import asyncio
import calendar
import json
import logging
import os
from datetime import date, timedelta
from math import ceil
from typing import Optional

from sqlalchemy import and_, func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.product import Product
from app.models.sales import SalesData
from app.models.daily_sales_stat import DailySalesStat
from app.models.inventory import InventorySnapshot
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.calculation import (
    CalculationResult,
    CalculationStepResult,
    CalculationSkipLog,
    CalculationTimelineReset,
)
from app.models.ai_evaluation import AiEvaluation
from app.models.operator import Operator
from app.services.time_axis import get_sales_phase, check_purchase_window, get_recommended_transport, get_festival_info, resolve_lead_time
from app.services.semantic_classify import buffer_days, get_semantic_classification
from app.services.forecast import (
    forecast_all_months,
    month_lifecycle_ratio,
    compute_trend_coeff,
    compute_ad_coeff,
    compute_listing_coeff,
    compute_market_coeff,
)
from app.services import new_product_policy
from app.services import ai_eval
from app.services.sales_fallback import (
    get_daily_sales_dual,
    sum_daily_sales_dual,
)

logger = logging.getLogger(__name__)

# P0-P4 与产品等级 S/A/B/C/D 的对应关系（calc_frequency 字段历史值）
_P_LEVEL_MAP = {"P0": "S", "P1": "A", "P2": "B", "P3": "C", "P4": "D"}
_FREQ_CACHE: dict | None = None

_ALL_LEVELS = ("S", "A", "B", "C", "D")


def _normalize_levels(level: str | None) -> list[str]:
    """将 "SAB" / "S,A,B" 归一为等级列表（只保留 S/A/B/C/D，去重保序）"""
    raw = (level or "").upper().replace("，", ",")
    if "," in raw:
        items = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        items = list(raw)
    return list(dict.fromkeys(p for p in items if p in _ALL_LEVELS))


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


def get_calculation_frequency_days(product: Product, freq_map: dict | None = None, default_freq: int | None = None) -> int:
    """获取产品计算频率（天）

    优先级：
    1) calc_frequency 直接写数字（如 "2"）时作为单产品覆盖；
    2) 按等级 S/A/B/C/D 读取 CALC_FREQUENCIES 配置（可自定义）；
    3) 未匹配时使用 CALC_FREQUENCY_DEFAULT。
    """
    if product.calc_frequency and product.calc_frequency.strip().isdigit():
        return int(product.calc_frequency.strip())
    if freq_map is None:
        freq_map = _parse_frequencies(settings.CALC_FREQUENCIES)
    level = get_product_level(product)
    return freq_map.get(level, default_freq if default_freq is not None else settings.CALC_FREQUENCY_DEFAULT)


async def _load_frequency_config(session: AsyncSession) -> tuple[dict, int]:
    """从配置表读取等级频率（DB 可覆盖 .env 默认值）"""
    from app.services.config_service import get_calc_frequencies, get_param

    freq_map = await get_calc_frequencies(session)
    default_freq = await get_param(session, "calc_frequency_default")
    if default_freq is None:
        default_freq = settings.CALC_FREQUENCY_DEFAULT
    return freq_map, int(default_freq)


def _normalize_lifecycles(raw) -> set[str]:
    """解析生命周期过滤（逗号分隔字符串 / list / set，支持中英文逗号）；空=不过滤，返回空集合"""
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set)):
        items: list[str] = []
        for x in raw:
            items.extend(str(x).replace("，", ",").split(","))
    else:
        items = str(raw).replace("，", ",").split(",")
    return {x.strip() for x in items if x.strip()}


async def _load_report_lifecycles(session: AsyncSession) -> set[str]:
    """读取日报分析生命周期自定义（逗号分隔，可多选）；空=全部生命周期不过滤，返回空集合"""
    from app.services.config_service import get_param

    raw = await get_param(session, "report_lifecycles")
    if raw is None:
        raw = settings.REPORT_LIFECYCLES
    return _normalize_lifecycles(raw)


async def _load_schedule_overrides(session: AsyncSession) -> dict[str, tuple[date, date]]:
    """读取排程重置(时间调节器)覆盖：{asin: (override_last_date, target_date)}"""
    rows = await session.execute(
        select(CalculationTimelineReset.asin, CalculationTimelineReset.override_last_date, CalculationTimelineReset.target_date)
    )
    return {asin: (ov_last, target) for asin, ov_last, target in rows.all()}


async def _load_effective_last_dates(session: AsyncSession, cleanup: bool = False) -> dict[str, date]:
    """获取每个 ASIN 的"有效最近计算日期"（排程层唯一数据源）。

    优先级：若存在排程重置且真实日期未越过 target_date，用 override_last_date 覆盖；
    否则用真实 max(calc_date)。cleanup=True 时顺手清掉已过期的重置记录。
    """
    rows = await session.execute(
        select(CalculationResult.asin, func.max(CalculationResult.calc_date))
        .group_by(CalculationResult.asin)
    )
    last_dates: dict[str, date] = {asin: d for asin, d in rows.all()}
    overrides = await _load_schedule_overrides(session)
    expired: list[str] = []
    for asin, (ov_last, target) in overrides.items():
        real = last_dates.get(asin)
        if real is not None and real >= target:
            expired.append(asin)  # 真实计算已越过目标，override 失效，恢复真实日期
            continue
        last_dates[asin] = ov_last
    if cleanup and expired:
        await session.execute(
            delete(CalculationTimelineReset).where(CalculationTimelineReset.asin.in_(expired))
        )
    return last_dates


async def reset_calculation_timeline(
    session: AsyncSession,
    levels: list[str] | None = None,
    target_date: date | None = None,
    commit: bool = True,
) -> dict:
    """排程重置(时间调节器)：把下次分析时间线拨到指定等级在 target_date 到期。

    不动 calc_frequencies 频率配置，只写排程重置覆盖。


    - 目标等级(levels)：有效最近日期设为 target_date - 频率，使其在 target_date 到期(拨快)；
    - 其余等级：若其下一次计算 <= target_date 会插队，则把有效最近日期设为今天，使其顺延到之后(拨慢)；
    - 真实 calc_date 一旦 >= target_date 自动过期，后续恢复常态排程。
    """
    levels = [lv.upper().strip() for lv in (levels or ["S", "A"])]
    levels = [lv for lv in levels if lv in _ALL_LEVELS]
    if not levels:
        raise ValueError("levels 必须包含 S/A/B/C/D 中的至少一个")

    today = date.today()
    if target_date is None:
        target_date = today + timedelta(days=1)
    if target_date <= today:
        raise ValueError("target_date 必须晚于今天")

    prods = (await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )).scalars().all()
    freq_map, default_freq = await _load_frequency_config(session)
    real_last = {asin: d for asin, d in (await session.execute(
        select(CalculationResult.asin, func.max(CalculationResult.calc_date))
        .group_by(CalculationResult.asin)
    )).all()}

    override_map: dict[str, tuple[date, date]] = {}
    skip = []
    for p in prods:
        level = get_product_level(p)
        freq = get_calculation_frequency_days(p, freq_map, default_freq)
        real = real_last.get(p.asin)
        if level in levels:
            # 拨快：目标等级在 target_date 到期
            if real is not None and real >= target_date:
                skip.append(p.asin)  # 已越过目标，无需重置
                continue
            ov_last = target_date - timedelta(days=freq)
            if ov_last > today:
                ov_last = today
            override_map[p.asin] = (ov_last, target_date)
        else:
            # 拨慢：其他等级若会在 target_date 前插队，则顺延到 target_date 之后
            if real is None or (today - real).days >= freq:
                current_next = today
            else:
                current_next = real + timedelta(days=freq)
            if current_next <= target_date:
                override_map[p.asin] = (today, target_date)

    # 先清掉这些 ASIN 的旧重置，再写新覆盖
    if override_map:
        await session.execute(
            delete(CalculationTimelineReset).where(CalculationTimelineReset.asin.in_(list(override_map.keys())))
        )
        for asin, (ov_last, target) in override_map.items():
            session.add(CalculationTimelineReset(
                asin=asin, override_last_date=ov_last, target_date=target
            ))

    if commit:
        await session.commit()
    else:
        await session.flush()

    logger.info("排程重置完成：levels=%s target=%s 覆盖 %d 条（跳过 %d 条已到期）",
                levels, target_date, len(override_map), len(skip))
    return {
        "target_date": target_date.isoformat(),
        "levels": levels,
        "overridden_count": len(override_map),
        "skipped_count": len(skip),
        "date_today": today.isoformat(),
    }


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


# 级别三档化：仅保留 立即采购/观察/暂停（建议采购=立即采购；未触发/终止=暂停）
LEVEL_3TIER = {
    "立即采购": "立即采购",
    "建议采购": "立即采购",
    "观察": "观察",
    "暂停": "暂停",
    "未触发": "暂停",
    "终止": "暂停",
}


def normalize_level(level) -> str | None:
    """级别列统一为三档：80-100立即采购 / 60-79观察 / 1-59暂停（及无评分决策映射）"""
    return LEVEL_3TIER.get(level or "", level)


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


def _parse_step_json(s: str | None):
    """JSON字符串转对象，用于API返回展示"""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


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


def _report_score(r) -> float | None:
    """日报展示评分：有 purchase_score 直接用；新品六维总分仅在已作出决策
    （非 未触发/终止）时回填，避免“未触发却显示高分”的矛盾"""
    if r.purchase_score is not None:
        return r.purchase_score
    if (r.purchase_level or "") in ("未触发", "终止"):
        return None
    return _six_dim_total(_parse_step_json(r.score_detail))


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


def _to_float_safe(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


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
                        f"，趋势系数={history.get('lt_trend_coeff')}")
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
        # 新品也计算六维评分（统一评分明细展示；等级仍以新品门禁为准）
        try:
            six = _calc_score(forecast, inventory, life_cycle, trigger, product, purchase_window, lifecycle_end, True)
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


# ──────────────────────────────────────────────
#  批量计算
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
#  日报汇总
# ──────────────────────────────────────────────

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


# ──────────────────────────────────────────────
#  内部实现函数
# ──────────────────────────────────────────────

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


async def _is_new_product(product: Product, session: AsyncSession) -> bool:
    """判断产品是否为新品（新品走专用门禁流程）

    特例优先：品名命中特例关键词（参数 new_product_name_keywords，默认 26版/27版）
      → 新品（老品 ASIN 复用场景）；
    其次 listing 标签命中老品特例标签（参数 product_type_old_tags，默认 19年前/18年前）
      → 老品；
    其次上架日期（list_date ≤365天=新品，>365天=老品）；
    list_date 缺失时才用 product_stage 兜底。
    """
    from app.services.config_service import get_param
    from app.services.product_stage import OLD_TAGS_DEFAULT, is_new_product_basic

    try:
        keywords = await get_param(session, "new_product_name_keywords") or "26版,27版"
    except Exception:  # noqa: BLE001
        keywords = "26版,27版"
    try:
        old_tags = await get_param(session, "product_type_old_tags")
    except Exception:  # noqa: BLE001
        old_tags = OLD_TAGS_DEFAULT
    return is_new_product_basic(product, keywords, old_tags)


async def _analyze_sales_history(
    asin: str, session: AsyncSession, product: Product = None, is_new: bool = False
) -> dict:
    """分析历史销量数据（老品含同比/环比；新品另给上架天数/近期单量/累计销量）

    数据源统一：daily_sales_stats（领星 sales-statistics/report/list，filterDateType=day
    逐日实抓，优先），该区间无任何记录时才回退 sales_data 明细。
    月均、去年同月、上月/上上月（同比/环比基准）统一由逐日数据按月汇总，保证口径一致；
    金额类指标（去年同月销售额/毛利/广告/ACOS）取自 historical_monthly_stats
    （逐日表无这些字段）。

    is_new=True（新品）时额外给出上架天数（list_date 起）、最近3天/7天单量、
    日均（最近7天合计÷实际天数，与新品补货策略基准同源同口径）、累计销量。
    """
    today = date.today()
    # 新品附加指标：上架日期（累计销量按上架日截断）
    list_date = getattr(product, "list_date", None) if product else None
    if hasattr(list_date, "date"):
        list_date = list_date.date()
    extra = {}
    if is_new:
        r3 = await _recent_daily_orders(asin, session, days=3)
        r7 = await _recent_daily_orders(asin, session, days=7)
        extra = {
            "list_date": str(list_date) if list_date else None,
            "days_on_sale": (today - list_date).days if list_date else None,
            "recent_3d_qty": sum(r3),
            "recent_7d_qty": sum(r7),
            "daily_avg_qty": round(sum(r7) / max(len(r7), 1), 2),
            "cumulative_sales": 0,
        }

    # 节日老品：去年同窗口（今年「明天」→ 今年 festival_end，整年 −1）各月逐日销量 + 占比 + 趋势系数
    # （新口径，Step 5 预测与节日窗口预估共用；无有效窗口时为 {}）
    fw = None if is_new else await _festival_sales_window(product, session, today)
    fw_fields = {
        "festival": fw["festival"],
        "window_start": fw["window_start"].isoformat(),
        "window_end": fw["window_end"].isoformat(),
        "monthly_sales": fw["monthly_sales"],
        "base_total": fw["base_total"],
        "recent_30d_qty": fw["recent_30d_qty"],
        "last_year_30d_qty": fw["last_year_30d_qty"],
        "trend_coeff": fw["trend_coeff"],
    } if fw else {}

    # 老品-长期产品：去年同窗口（明天→当前月+2 月末）各月逐日销量 + 占比 + 趋势系数
    # （Q8 口径；仅长期产品参与，老品无节日且非长期的本轮不处理，无有效窗口时为 {}）
    lt = None if is_new else await _long_term_sales_window(product, session, today)
    lt_fields = {
        "lt_window_start": lt["window_start"].isoformat(),
        "lt_window_end": lt["window_end"].isoformat(),
        "lt_last_window_start": lt["last_window_start"].isoformat(),
        "lt_last_window_end": lt["last_window_end"].isoformat(),
        "lt_monthly_sales": lt["monthly_sales"],
        "lt_base_total": lt["base_total"],
        "lt_recent_30d_qty": lt["recent_30d_qty"],
        "lt_last_year_30d_qty": lt["last_year_30d_qty"],
        "lt_trend_coeff": lt["trend_coeff"],
    } if lt else {}

    # 历史月度统计：仅用于金额类指标
    from app.models.historical_monthly import HistoricalMonthlyStats

    hist_rows = (await session.execute(
        select(HistoricalMonthlyStats).where(HistoricalMonthlyStats.asin == asin)
    )).scalars().all()
    hist = {(r.month, r.source): r for r in hist_rows}
    _h = lambda m, src="lingxing": hist.get((m, src))  # noqa: E731

    lym = f"{today.year - 1}-{today.month:02d}"   # 去年同月
    lm = _prev_month_key(today)                    # 上月
    pvm = _prev_month_key(today - timedelta(days=28))  # 上上月（环比基准）
    lpm = _prev_month_key(date(today.year - 1, today.month, 1))  # 去年上月（同比基准）

    # 抓取窗口回溯到「去年上月」1号，保证上述四个基准月都被完整覆盖
    _lpm_y, _lpm_m = (int(x) for x in lpm.split("-"))
    window_start = date(_lpm_y, _lpm_m, 1)

    # 逐日销量：daily_sales_stats 优先（逐日实抓，最准确），无记录才回退 sales_data
    series = await get_daily_sales_dual(asin, window_start, today, session, use_zero=False)

    if not series:
        # 两源均无记录：返回零值结构（金额类指标仍取月度表）
        ly = _h(lym)
        return {
            "total": 0,
            "monthly_avg": 0,
            "monthly_data": {},
            "last_year_same_month": 0,
            "last_year_same_month_amount": (ly.sale_amount if ly else None),
            "last_year_same_month_gross_profit": (ly.gross_profit if ly else None),
            "last_year_same_month_ad_spend": (ly.ad_spend if ly else None),
            "last_year_same_month_acos": (ly.acos if ly else None),
            "last_month_sales": 0,
            "prev_month_sales": 0,
            "yoy_sales_pct": None,
            "mom_sales_pct": None,
            "recent_3_months_avg": 0,
            **fw_fields,
            **lt_fields,
            **extra,
        }

    total = sum(series.values())

    # 按月分组（月均/同比/环比统一从这里汇总）
    monthly_data = {}
    for d, q in series.items():
        key = f"{d.year}-{d.month:02d}"
        monthly_data[key] = monthly_data.get(key, 0) + q

    # 月均：按真实覆盖天数折算（有数据区间的天数 / 30），不再把不足一月当整月
    days_covered = (max(series) - min(series)).days + 1
    monthly_avg = round(total / (days_covered / 30)) if days_covered > 0 else 0

    # 去年同月销量（统一取逐日表按月汇总；金额类指标仍来自月度表）
    ly = _h(lym)
    last_year_same_month = monthly_data.get(lym, 0)

    # 上月/上上月/去年上月（同比、环比基准，统一取逐日表按月汇总）
    last_month_sales = monthly_data.get(lm, 0)
    prev_month_sales = monthly_data.get(pvm, 0)
    last_year_prev_month_sales = monthly_data.get(lpm, 0)

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

    # 新品累计销量：上架日起（list_date 缺失则取整个抓取窗口）
    if is_new:
        extra["cumulative_sales"] = sum(
            q for d, q in series.items() if (not list_date) or d >= list_date
        )

    return {
        "total": total,
        "monthly_avg": monthly_avg,
        "monthly_data": monthly_data,
        "last_year_same_month": last_year_same_month,
        "last_year_same_month_amount": (ly.sale_amount if ly else None),
        "last_year_same_month_gross_profit": (ly.gross_profit if ly else None),
        "last_year_same_month_ad_spend": (ly.ad_spend if ly else None),
        "last_year_same_month_acos": (ly.acos if ly else None),
        "last_month_sales": last_month_sales,
        "prev_month_sales": prev_month_sales,
        "yoy_sales_pct": _calc_yoy(last_month_sales, last_year_prev_month_sales),
        "mom_sales_pct": _calc_yoy(last_month_sales, prev_month_sales),
        "recent_3_months_avg": recent_3_months_avg,
        **fw_fields,
        **lt_fields,
        **extra,
    }


def _prev_month_key(d: date) -> str:
    """上月 YYYY-MM"""
    y, m = d.year, d.month - 1
    if m == 0:
        y -= 1
        m = 12
    return f"{y}-{m:02d}"


def _calc_yoy(cur_v, base_v) -> float | None:
    """同比/环比涨跌幅（%）：当前 vs 基准；基准≤0 返回 None"""
    if cur_v is None or base_v is None or base_v <= 0:
        return None
    return round((float(cur_v) - float(base_v)) / float(base_v) * 100, 1)


def _shift_year(d: date, years: int) -> date:
    """按年平移日期（2-29 落到非闰年时取 2-28）"""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def _month_keys_between(start: date, end: date) -> list[int]:
    """窗口 [start, end] 覆盖的月份（按时间顺序，跨年时为 [12, 1] 这类序列）"""
    months: list[int] = []
    cur = date(start.year, start.month, 1)
    last = date(end.year, end.month, 1)
    while cur <= last:
        months.append(cur.month)
        cur = date(cur.year + 1, 1, 1) if cur.month == 12 else date(cur.year, cur.month + 1, 1)
    return months


def _month_end(d: date, offset_months: int = 0) -> date:
    """d 所在月再偏移 offset_months 个月的月末（offset=0 即当月月末）"""
    idx = d.month - 1 + offset_months
    y = d.year + idx // 12
    m = idx % 12 + 1
    return date(y, m, calendar.monthrange(y, m)[1])


async def _festival_sales_window(
    product: Product, session: AsyncSession, today: date | None = None
) -> dict | None:
    """节日老品：去年同窗口逐日销量按月汇总 + 各月占比 + 趋势系数（Step 4 / Step 5 / 窗口预估共用）

    用户确认口径：
      1. 今年预测周期 = [明天, 今年节日结束]，去年同期 = [去年明天, 去年节日结束]，
         整个窗口年份 −1（不加任何偏移）；
      2. 「节日结束」= festival_calendar.festival_end（预设节日结束时间），
         与「生命周期最后阶段（下降期）结束日」是不同的概念；
      3. 各月销量 = 去年窗口内**逐日求和**，**首尾月为不完整月**
         （首月自 window_start 起、末月截至 window_end）；
      4. 各月占比 = 该月销量 ÷ 窗口合计（基准一致）；
      5. 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0）。

    返回 None：非节日产品 / 长期产品 / 未匹配到 festival_calendar /
    festival_end 缺失或已早于「明天」（该情况本轮不处理，由人工修订表数据）。
    """
    from app.models.festival_calendar import FestivalCalendar
    from app.services.festival_lifecycle import match_festival_name

    today = today or date.today()
    festival = getattr(product, "festival", None)
    if not festival:
        return None
    # 长期产品全年销售不过季，不参与节日窗口
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
        # 与 get_festival_info / _calc_festival_window 同口径：取节日日距当前最近的一条
        best, best_gap = recs[0], None
        for r in recs:
            target = r.festival_date or r.listing_start or r.festival_end
            if target is None:
                continue
            gap = abs((target.date() - today).days)
            if best_gap is None or gap < best_gap:
                best, best_gap = r, gap
        rec = best

    if not rec.festival_end:
        return None
    window_start = today + timedelta(days=1)   # 今年预测周期起点：明天
    window_end = rec.festival_end.date()       # 今年节日结束（festival_calendar 预设）
    if window_end < window_start:
        # 表内年份尚未滚动到下一周期（约 22 行），本轮不自动顺延，退回原逻辑
        return None

    last_start = _shift_year(window_start, -1)
    last_end = _shift_year(window_end, -1)

    # 去年窗口各月逐日求和（双源：daily_sales_stats 优先，无记录回退 sales_data）
    series = await get_daily_sales_dual(product.asin, last_start, last_end, session, use_zero=False)
    monthly: dict[str, int] = {}
    for d, q in series.items():
        key = f"{d.year}-{d.month:02d}"
        monthly[key] = monthly.get(key, 0) + q
    base_total = sum(monthly.values())
    monthly_sales = [
        {
            "month": k,
            "qty": v,
            "share_pct": round(v / base_total * 100, 1) if base_total > 0 else 0.0,
        }
        for k, v in sorted(monthly.items())
    ]

    # 趋势系数分子分母：今年近30天 / 去年同30天
    this_30d_start = today - timedelta(days=29)
    recent_30d_qty = await sum_daily_sales_dual(product.asin, this_30d_start, today, session) or 0
    last_year_30d_qty = await sum_daily_sales_dual(
        product.asin, _shift_year(this_30d_start, -1), _shift_year(today, -1), session
    ) or 0
    trend_coeff = round(recent_30d_qty / last_year_30d_qty, 4) if last_year_30d_qty > 0 else 1.0

    return {
        "festival": name,
        "festival_year": window_end.year,
        "window_start": window_start,
        "window_end": window_end,
        "last_window_start": last_start,
        "last_window_end": last_end,
        "window_months": _month_keys_between(window_start, window_end),
        "monthly_sales": monthly_sales,
        "base_total": base_total,
        "recent_30d_qty": recent_30d_qty,
        "last_year_30d_qty": last_year_30d_qty,
        "trend_coeff": trend_coeff,
        "hot_end_month": int(rec.hot_end_month or window_end.month),
    }


async def _long_term_sales_window(
    product: Product, session: AsyncSession, today: date | None = None
) -> dict | None:
    """老品-长期产品：去年同窗口逐月销量 + 各月占比 + 趋势系数（Q8 口径，Step 4/5/9 共用）

    用户确认口径：
      1. 今年预测周期 = [明天, 当前月份+2 的月末]，共 3 个月；
         首月（当前月）通常不是完整月（除非今天为 1 号），第 2、3 个月为完整月；
      2. 去年同期窗口 = 整个窗口年份 −1（逐日求和，首尾月同样为不完整月）；
      3. 各月销量 = 去年窗口内**逐日求和**；各月占比 = 该月销量 ÷ 窗口合计；
      4. 趋势系数 = 最近30天销量 ÷ 去年同期30天销量（去年为 0 → 1.0）；
      5. 未来预测总销量 = 去年同期窗口基数 × 趋势系数；
         当月预测销量 = 未来预测总销量 × 去年该月占比（Step 5 产出、Step 9 批次规划用）。

    返回 None：新品由调用方排除 / 非长期产品（老品无节日且非长期本轮不处理）/ ASIN 缺失。
    """
    today = today or date.today()
    if not product or not getattr(product, "asin", None):
        return None
    # 仅「老品长期产品」参与；老品无节日且非长期的本轮不处理
    if (getattr(product, "product_type", "") or "").strip() != "长期产品":
        return None

    window_start = today + timedelta(days=1)   # 今年预测周期起点：明天
    window_end = _month_end(today, 2)          # 当前月份+2 的月末
    last_start = _shift_year(window_start, -1)
    last_end = _shift_year(window_end, -1)

    # 去年窗口各月逐日求和（双源：daily_sales_stats 优先，无记录回退 sales_data）
    series = await get_daily_sales_dual(product.asin, last_start, last_end, session, use_zero=False)
    monthly: dict[str, int] = {}
    for d, q in series.items():
        key = f"{d.year}-{d.month:02d}"
        monthly[key] = monthly.get(key, 0) + q
    base_total = sum(monthly.values())
    monthly_sales = [
        {
            "month": k,
            "qty": v,
            "share_pct": round(v / base_total * 100, 1) if base_total > 0 else 0.0,
        }
        for k, v in sorted(monthly.items())
    ]

    # 趋势系数分子分母：今年近30天 / 去年同30天
    this_30d_start = today - timedelta(days=29)
    recent_30d_qty = await sum_daily_sales_dual(product.asin, this_30d_start, today, session) or 0
    last_year_30d_qty = await sum_daily_sales_dual(
        product.asin, _shift_year(this_30d_start, -1), _shift_year(today, -1), session
    ) or 0
    trend_coeff = round(recent_30d_qty / last_year_30d_qty, 4) if last_year_30d_qty > 0 else 1.0

    return {
        "window_start": window_start,
        "window_end": window_end,
        "last_window_start": last_start,
        "last_window_end": last_end,
        "window_months": _month_keys_between(window_start, window_end),
        "monthly_sales": monthly_sales,
        "base_total": base_total,
        "recent_30d_qty": recent_30d_qty,
        "last_year_30d_qty": last_year_30d_qty,
        "trend_coeff": trend_coeff,
    }


async def _build_forecast_coefficients(product: Product, session: AsyncSession) -> dict:
    """从真实数据构建预测修正系数（趋势/市场/广告/Listing）

    数据源：最近两次销量快照（7/14/30天销量、类目排名、SIF评分/评论增长）+ 产品档案（广告花费/ACOS）
    """
    rows = (await session.execute(
        select(DailySalesSnapshot)
        .where(DailySalesSnapshot.asin == product.asin)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(2)
    )).scalars().all()
    latest = rows[0] if rows else None
    prev = rows[1] if len(rows) > 1 else None

    # 7天销量：领星从不返回 seven_volume（恒0），改用 average_seven_volume×7 还原
    list_seven = round((latest.average_seven_volume or 0) * 7) if latest else 0
    trend = compute_trend_coeff(
        list_seven,
        latest.fourteen_volume if latest else 0,
        latest.thirty_volume if latest else 0,
    )

    ad_ratio = None
    thirty_amount = _to_float_safe(getattr(product, "thirty_amount", None))
    thirty_spend = _to_float_safe(getattr(product, "thirty_spend", None))
    if thirty_amount and thirty_spend and thirty_amount > 0:
        ad_ratio = round(thirty_spend / thirty_amount, 4)
    acos = getattr(product, "acos_30d", None)
    ad = compute_ad_coeff(ad_ratio, acos)

    rating = None
    review_growth = None
    if latest is not None:
        rating = latest.sif_rating if latest.sif_rating is not None else getattr(product, "stars", None)
        review_growth = latest.sif_review_growth
    listing = compute_listing_coeff(rating, review_growth)

    rank = latest.category_rank if latest else None
    prev_rank = prev.category_rank if prev else None
    market = compute_market_coeff(rank, prev_rank)

    return {
        "trend_coeff": trend,
        "market_coeff": market,
        "ad_coeff": ad,
        "listing_coeff": listing,
        "detail": {
            "trend": trend,
            "market": market,
            "ad": ad,
            "listing": listing,
            "ad_ratio": ad_ratio,
            "acos": acos,
            "rating": rating,
            "review_growth": review_growth,
            "category_rank": rank,
            "prev_rank": prev_rank,
        },
    }


async def _forecast_sales_new_product(product: Product, session: AsyncSession) -> dict:
    """新品未来销量预测（需求文档第六章口径；不再预测六个月）

    - 节日产品: 预测日销 = 基准（最近N天日均单量）× 生命周期日销系数 × 安全系数，
      启动期按 系数^(经过天数÷10) 逐日增长；窗口 = 今天 → 节日生命周期结束日
      （无结束日时取需求截止日：节日日期 − 装饰14天/非装饰3天），逐日求和；
    - 长期产品 / 无节日新品: 预测日销 = 基准 × 安全系数（不乘生命周期系数），
      窗口 = 默认补货周期（工期 7-10→45天、11-20→50天、21+→60天）。
    """
    cfg = await _get_new_product_cfg(session)
    trigger_days = int(cfg.get("trigger_days", 3))
    daily_orders = await _recent_daily_orders(product.asin, session, days=max(trigger_days, 7))
    recent = daily_orders[-trigger_days:]
    base_daily = sum(recent) / max(len(recent), 1)

    today = date.today()
    life_cycle = product.life_cycle or ""
    is_long_term = (getattr(product, "product_type", "") or "").strip() == "长期产品"
    lead_time = await resolve_lead_time(product, session)

    festival_date = None
    if product.festival:
        info = await get_festival_info(product.festival, session)
        festival_date = info.get("festival_date") if info else None
    use_festival = bool(festival_date) and not is_long_term

    plan = None
    launch_start = None
    if use_festival:
        from app.services.festival_lifecycle import launch_stage_start, lifecycle_end_date

        # 缓存天数判定依据：AI 语义分类（装饰品/非装饰品）
        is_decoration = (await get_semantic_classification(session, product.asin) or "").strip() == "装饰品"
        launch_start = await launch_stage_start(product, session, today)
        window_end = await lifecycle_end_date(product, session, today) or \
            new_product_policy.calc_demand_deadline(festival_date, is_decoration)
        branch = "节日产品"
        model = "new_product_festival_window"
        basis = (f"基准={base_daily:.2f}（最近{trigger_days}天单量{recent}的日均）"
                 f"× 生命周期系数 × 安全系数")
    else:
        # 长期产品 / 无节日新品：预测日销 = 基准 × 安全系数（不乘生命周期系数），窗口=默认补货周期
        safety = float(cfg.get("long_term_safety_factor", 1.2))
        plan = new_product_policy.long_term_replenishment_plan(
            base_daily_sales=base_daily, available_stock=0, lead_time=lead_time,
            safety_factor=safety, current_date=today,
        )
        cycle = int(plan.get("replenishment_cycle") or 45)
        window_end = today + timedelta(days=cycle)
        branch = "长期产品" if is_long_term else "无节日新品（长期口径）"
        model = "new_product_long_term_cycle"
        basis = (f"基准={base_daily:.2f}（最近{trigger_days}天单量{recent}的日均）"
                 f"× 安全系数{safety} × 补货周期{cycle}天")

    # 窗口逐日求和（节日走生命周期系数、长期为常数日销），并按月汇总
    total = 0.0
    month_sum: dict[str, float] = {}
    day = today
    while day < window_end:
        if use_festival:
            elapsed = (day - launch_start).days if launch_start else 0
            qty = new_product_policy.daily_forecast_by_stage(base_daily, life_cycle, elapsed)
        else:
            qty = float(plan.get("forecast_daily") or 0)
        total += qty
        key = f"{day.year}-{day.month:02d}"
        month_sum[key] = month_sum.get(key, 0.0) + qty
        day += timedelta(days=1)

    return {
        "forecast_months": [
            {"month": k, "forecast_qty": round(v), "seasonal_factor": 1.0, "days_ratio": 1.0}
            for k, v in sorted(month_sum.items())
        ],
        "forecast_total": round(total),
        "forecast_model": model,
        "forecast_branch": branch,
        "forecast_basis": basis,
        "forecast_window": {
            "start": today.isoformat(),
            "end": window_end.isoformat(),
            "days": (window_end - today).days,
        },
        "launch_start": launch_start.isoformat() if launch_start else None,
        "base_daily_sales": round(base_daily, 2),
        "life_cycle": life_cycle,
    }


def _forecast_sales_festival(history: dict) -> dict | None:
    """节日老品未来销量预测（用户确认口径，Step 4 提供基数与趋势系数）

    - 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0，已在 Step 4 兜底）；
    - 未来总量 = 去年窗口合计（基数）× 趋势系数；
    - 各月预测 = 去年该月（窗口内逐日求和口径，首尾月为不完整月）× 趋势系数，
      保留季节性分布，Σ = 基数 × 趋势系数。

    Step 4 未给出有效窗口（无节日/长期产品/festival_end 缺失或已过期）时返回 None，退回原模型。
    """
    monthly = history.get("monthly_sales") or []
    if not monthly:
        return None
    trend = history.get("trend_coeff")
    try:
        trend = float(trend) if trend else 1.0
    except (TypeError, ValueError):
        trend = 1.0
    if trend <= 0:
        trend = 1.0

    forecast_months = []
    for item in monthly:
        month = item.get("month") or ""
        y, _, m = month.partition("-")
        if not y or not m:
            continue
        forecast_months.append({
            "month": f"{int(y) + 1}-{m}",                      # 去年窗口月份 → 今年同月
            "forecast_qty": round((item.get("qty") or 0) * trend),
            "seasonal_factor": 1.0,
            "days_ratio": 1.0,
        })
    if not forecast_months:
        return None
    return {
        "forecast_months": forecast_months,
        "forecast_total": sum(m["forecast_qty"] for m in forecast_months),
        "forecast_model": "festival_window_forecast",
        "forecast_coeffs": {"trend": round(trend, 4)},
    }


def _forecast_sales_long_term(history: dict) -> dict | None:
    """老品-长期产品未来销量预测（Q8 口径，Step 4 提供基数、趋势系数与各月占比）

    - 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0，已在 Step 4 兜底）；
    - 未来预测总销量 = 去年同期窗口基数（window 合计）× 趋势系数；
    - 当月预测销量 = 未来预测总销量 × 去年该月占比（Σ 各月 = 总销量）；
    - 各月占比一并保留（Step 9 采购批次规划用）。

    Step 4 未给出有效窗口（非长期产品 / 新品）时返回 None，退回原模型。
    """
    monthly = history.get("lt_monthly_sales") or []
    if not monthly:
        return None
    try:
        trend = float(history.get("lt_trend_coeff") or 1.0)
    except (TypeError, ValueError):
        trend = 1.0
    if trend <= 0:
        trend = 1.0
    try:
        base_total = int(history.get("lt_base_total") or 0)
    except (TypeError, ValueError):
        base_total = 0
    forecast_total = round(base_total * trend)

    forecast_months = []
    for item in monthly:
        month = item.get("month") or ""
        y, _, m = month.partition("-")
        if not y or not m:
            continue
        try:
            share = float(item.get("share_pct") or 0.0)
        except (TypeError, ValueError):
            share = 0.0
        forecast_months.append({
            "month": f"{int(y) + 1}-{m}",                        # 去年窗口月份 → 今年同月
            "forecast_qty": round(forecast_total * share / 100),  # 总销量 × 去年该月占比
            "seasonal_factor": 1.0,
            "days_ratio": 1.0,
            "share_pct": round(share, 1),
        })
    if not forecast_months:
        return None
    return {
        "forecast_months": forecast_months,
        "forecast_total": forecast_total,
        "forecast_model": "long_term_window_forecast",
        "forecast_coeffs": {"trend": round(trend, 4), "base_total": base_total},
    }


async def _forecast_sales(product: Product, history: dict, session: AsyncSession) -> dict:
    """预测未来销量（老品模型；新品走第六章窗口口径）

    老品-节日: 去年同窗口各月销量 × 趋势系数（见 _forecast_sales_festival，基数与趋势系数由 Step 4 提供）
    老品-长期产品: 未来预测总销量 = 去年同期窗口基数 × 趋势系数，
                当月预测销量 = 未来预测总销量 × 去年该月占比（见 _forecast_sales_long_term）
    老品-无节日且非长期: old_product_forecast
                （历史40% + 趋势25% + 市场15% + 广告10% + Listing10%）
    新品: 见 _forecast_sales_new_product（基准×生命周期系数×安全系数，窗口至活动结束）
    主路径无有效数据 → 唯一的兜底是 DeepSeek AI 预测（见 _forecast_sales_ai）；
    AI 也无有效数据 → 视为无有效预测（forecast_total=0，不再产出 legacy 简化预测）。
    """
    is_new = await _is_new_product(product, session)
    if is_new:
        return await _forecast_sales_new_product(product, session)

    # 节日老品：直接用 Step 4 的「基数 + 趋势系数」新口径（Q4 统一口径，不再另算一套基准）
    festival_result = _forecast_sales_festival(history)
    if festival_result is not None:
        return festival_result

    # 老品-长期产品：去年同期窗口基数 × 趋势系数（Q8 口径，各月占比来自 Step 4）
    long_term_result = _forecast_sales_long_term(history)
    if long_term_result is not None:
        return long_term_result

    coeffs = await _build_forecast_coefficients(product, session)
    result = None
    try:
        result = await forecast_all_months(
            asin=product.asin,
            forecast_months=settings.FORECAST_MONTHS,
            session=session,
            is_new_product=is_new,
            trend_coeff=coeffs["trend_coeff"],
            market_coeff=coeffs["market_coeff"],
            ad_coeff=coeffs["ad_coeff"],
            listing_coeff=coeffs["listing_coeff"],
        )
        forecast_total = result.get("total", 0) or 0
        if forecast_total > 0 and result.get("monthly"):
            forecast_months = []
            for m in result["monthly"]:
                month_label = m["month"][:7]  # "2026-09-01" -> "2026-09"
                forecast_months.append({
                    "month": month_label,
                    "forecast_qty": m["forecast"],
                    "seasonal_factor": 1.0,
                    # 节日生命周期结束月的剩余天数折算比例（1.0=整月）
                    "days_ratio": m.get("days_ratio", 1.0),
                })
            result = {
                "forecast_months": forecast_months,
                "forecast_total": forecast_total,
                "forecast_model": "old_product_forecast",
                "forecast_coeffs": coeffs["detail"],
            }
        else:
            logger.warning(f"[{product.asin}] forecast_all_months 无有效预测(total={forecast_total})，回退旧模型")
            result = None
    except Exception as e:
        logger.warning(f"[{product.asin}] forecast_all_months 失败: {e}，转 AI 预测兜底")

    # 规则预测条件不足 → 用 DeepSeek AI 预测兜底（本函数是唯一兜底路径，见 _forecast_sales_ai 说明）
    if result is None:
        # 兜底路径同样做节日生命周期截断（日粒度），避免超额预测
        from app.services.festival_lifecycle import lifecycle_end_date
        lifecycle_end = await lifecycle_end_date(product, session)
        ai_forecast = await _forecast_sales_ai(product, session, lifecycle_end)
        if ai_forecast:
            result = ai_forecast
        else:
            # AI 也未给出有效预测 → 视为无有效预测（forecast_total=0、无月度明细），
            # 不再产出 legacy 简化预测，避免与主路径（历史同期销量×综合修正系数）两套口径并存；
            # 下游按"无预测"逻辑处理（如 _calc_purchase_trigger 退回领星可售天数/无需求不触发采购）。
            logger.warning(f"[{product.asin}] 规则预测与 AI 预测均无有效数据，按无有效预测处理（总量=0）")
            result = {
                "forecast_months": [],
                "forecast_total": 0,
                "forecast_model": "no_forecast",
            }

    # 老品历史基准模型（历史40%/趋势25%/市场15%/广告10%/Listing10%）已反映实际趋势，
    # 不再叠加生命周期系数（新品已在 _forecast_sales_new_product 内按阶段系数逐日计算）
    forecast_months = result.get("forecast_months") or []
    result["forecast_months"] = forecast_months
    result["forecast_total"] = sum(m.get("forecast_qty", 0) for m in forecast_months)
    return result


async def _forecast_sales_ai(
    product: Product, session: AsyncSession, lifecycle_end=None
) -> dict | None:
    """规则预测无有效数据时，调用 DeepSeek AI 预测未来销量（失败返回 None）

    本函数是预测的**唯一兜底**（legacy 简化模型已弃用）：老品口径统一以主路径
    old_product_forecast（历史同期销量 × 综合修正系数）为准，除 AI 外不再有其它兜底。
    返回 None（AI 未启用 / 无实际销量被跳过 / 调用失败 / 未给出有效总量）即视为
    「无有效预测」，由 _forecast_sales 返回 forecast_total=0，下游按无预测逻辑处理。

    lifecycle_end：节日生命周期结束日，传入时做日粒度截断（结束月按剩余天数折算），
    与 forecast_all_months 保持同一口径，避免兜底路径超额预测。
    """
    if not ai_eval.ai_enabled():
        return None
    # 数据门槛：仅当产品存在实际销量（近30天销量>0 或有任何销售明细）时才启用 AI 预测兜底；
    # 0销量/无历史产品 AI 预测纯属猜测，不应作为采购触发依据
    # （修复 B0HBVCZ62L/B0HDP5H3TT/B0HF7SGZPS 等 0 销量产品被 AI 猜预测误判"立即采购"）
    snap = (await session.execute(
        select(DailySalesSnapshot)
        .where(DailySalesSnapshot.asin == product.asin)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()
    has_sales = bool(snap and _safe_int(snap.thirty_volume) > 0)
    if not has_sales:
        total_qty = (await session.execute(
            select(func.coalesce(func.sum(SalesData.sales_qty), 0))
            .where(SalesData.asin == product.asin)
        )).scalar_one() or 0
        has_sales = total_qty > 0
    if not has_sales:
        # 双源优先：daily_sales_stats（逐日实抓）近一年是否有历史销量
        fb_total = await sum_daily_sales_dual(
            product.asin, date.today() - timedelta(days=365), date.today(), session
        )
        if fb_total:
            has_sales = True
    if not has_sales:
        logger.info(f"[{product.asin}] 无实际销量数据，跳过 AI 预测（避免 0 销量产品误触发采购）")
        return None
    try:
        eval_result = await ai_eval.evaluate_purchase(product.asin, session)
        forecast_info = eval_result.get("forecast") or {}
        total = int(forecast_info.get("suggested_forecast_total") or 0)
        if total <= 0:
            logger.info(f"[{product.asin}] AI 未给出有效预测总量，跳过")
            return None
        months = settings.FORECAST_MONTHS
        base = total // months
        today = date.today()
        forecast_months = []
        for i in range(months):
            m = today.month + i
            y = today.year + (m - 1) // 12
            m = ((m - 1) % 12) + 1
            # 节日生命周期结束日截断（日粒度）：结束月按剩余天数折算，其后月份不再计入
            ratio = month_lifecycle_ratio(y, m, lifecycle_end)
            if ratio <= 0:
                break
            month_qty = round(base * ratio) if ratio < 1 else base
            forecast_months.append({
                "month": f"{y}-{m:02d}",
                "forecast_qty": month_qty,
                "seasonal_factor": 1.0,
                "days_ratio": round(ratio, 4),
            })
        ai_total = sum(m["forecast_qty"] for m in forecast_months)
        logger.info(
            f"[{product.asin}] AI 预测成功: 未来{len(forecast_months)}月总量={ai_total}"
            f"{f'（截止节日生命周期结束 {lifecycle_end.isoformat()}）' if lifecycle_end else ''}（deepseek）"
        )
        return {
            "forecast_months": forecast_months,
            "forecast_total": ai_total,
            "forecast_model": "ai_deepseek",
        }
    except Exception as e:
        logger.warning(f"[{product.asin}] AI 预测失败: {e}")
        return None


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


SECOND_PEAK_APPROACH_DAYS = 30  # 补货后库存覆盖到第二个高峰期开始前 N 天，视为“临近”


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


async def _get_new_product_cfg(session: AsyncSession) -> dict:
    """读取新品策略配置（DB 可覆盖默认值），并应用到 new_product_policy"""
    from app.services.config_service import get_param

    async def g(key, default):
        v = await get_param(session, key)
        return v if v is not None else default

    cfg = {
        "trigger_days": int(await g("new_product_trigger_days", 3)),
        "trigger_min_order": float(await g("new_product_trigger_min_order", 5.0)),
        "acos_max": float(await g("new_product_acos_max", 0.55)),
        "min_selling_days": int(await g("new_product_min_selling_days", 14)),
        "decoration_buffer_days": int(await g("decoration_buffer_days", 14)),
        "non_decoration_buffer_days": int(await g("non_decoration_buffer_days", 3)),
        "long_term_safety_factor": float(await g("long_term_safety_factor", 1.2)),
        "usd_cny_rate": float(await g("usd_cny_rate", 7.2)),
        # 成本表（三渠道 Profit）模板参数
        "cost_exchange_rate": float(await g("cost_exchange_rate", settings.COST_EXCHANGE_RATE)),
        "cost_referral_ratio": float(await g("cost_referral_ratio", settings.COST_REFERRAL_RATIO)),
        "cost_referral_min": float(await g("cost_referral_min", settings.COST_REFERRAL_MIN)),
        "cost_packing_ratio": float(await g("cost_packing_ratio", settings.COST_PACKING_RATIO)),
        "cost_misc_ratio": float(await g("cost_misc_ratio", settings.COST_MISC_RATIO)),
        "cost_ad_ratio": float(await g("cost_ad_ratio", settings.COST_AD_RATIO)),
        "cost_return_ratio": float(await g("cost_return_ratio", settings.COST_RETURN_RATIO)),
        "cost_inbound_fee": float(await g("cost_inbound_fee", settings.COST_INBOUND_FEE)),
        "cost_storage_fee": float(await g("cost_storage_fee", settings.COST_STORAGE_FEE)),
        "cost_return_threshold": float(await g("cost_return_threshold", settings.COST_RETURN_THRESHOLD)),
        "cost_over_threshold_fee": float(await g("cost_over_threshold_fee", settings.COST_OVER_THRESHOLD_FEE)),
        "cost_default_weight_kg": float(await g("cost_default_weight_kg", settings.COST_DEFAULT_WEIGHT_KG)),
        # 成本表单件附加费用（美元/件，JSON），矫正三渠道Profit时逐项扣减
        "extra_fees_usd": new_product_policy._parse_extra_fees(await g("cost_extra_fees_usd", settings.COST_EXTRA_FEES_USD)),
        "transport_modes": {
            "sea": {
                "label": "海运",
                "slow_days": settings.SEA_SLOW_DAYS, "peak_days": settings.SEA_PEAK_DAYS,
                "slow_fee": float(await g("sea_slow_fee", 15.0)), "peak_fee": float(await g("sea_peak_fee", 17.0)),
            },
            "air": {
                "label": "空派",
                "slow_days": settings.AIR_SLOW_DAYS, "peak_days": settings.AIR_PEAK_DAYS,
                "slow_fee": float(await g("air_slow_fee", 60.0)), "peak_fee": float(await g("air_peak_fee", 70.0)),
            },
            "express": {
                "label": "快递",
                "slow_days": settings.EXPRESS_SLOW_DAYS, "peak_days": settings.EXPRESS_PEAK_DAYS,
                "slow_fee": float(await g("express_slow_fee", 70.0)), "peak_fee": float(await g("express_peak_fee", 80.0)),
            },
        },
    }
    new_product_policy.apply_config(cfg)
    return cfg


def _deep_find_acos(data) -> Optional[float]:
    """深度查找 ACOS 数值（返回结构不固定）"""
    if data is None:
        return None
    if isinstance(data, dict):
        for key, val in data.items():
            if "acos" in str(key).lower() and isinstance(val, (int, float)) and not isinstance(val, bool):
                try:
                    v = float(val)
                    if 0 <= v <= 5:  # 0-500% 范围
                        return v
                except (TypeError, ValueError):
                    pass
        for val in data.values():
            found = _deep_find_acos(val)
            if found is not None:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _deep_find_acos(item)
            if found is not None:
                return found
    return None


async def _fetch_acos(product: Product) -> Optional[float]:
    """获取30天ACOS：优先产品档案 acos_30d（已由 scripts/sync_acos.py 回填）

    不再逐产品调 SIF：SIF 广告结构接口不返回 ACOS，且外部调用是批量计算的主要耗时点。
    """
    if getattr(product, "acos_30d", None) is not None:
        try:
            return float(product.acos_30d)
        except (ValueError, TypeError):
            pass
    return None


async def _recent_daily_orders(asin: str, session: AsyncSession, days: int = 7) -> list:
    """最近N天每日销量/单量（按日期升序）"""
    start = date.today() - timedelta(days=days)
    rows = await session.execute(
        select(SalesData.date, SalesData.sales_qty)
        .where(SalesData.asin == asin, SalesData.date >= start)
        .order_by(SalesData.date)
    )
    vals = [int(r[1] or 0) for r in rows.all()]
    if not vals:
        # 双源优先：SalesData 无近N天记录时，回退 daily_sales_stats 逐日
        fb = await get_daily_sales_dual(asin, start, date.today(), session, use_zero=False)
        vals = [q for _d, q in sorted(fb.items())]
        if not vals:
            vals = [0]
    return vals


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
      3. 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0）；
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

    # ── 今年窗口总销量 = 窗口基准 × (1 + G) ──
    window_estimate = round(baseline * (1 + g))

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
        month_estimate[m] = round(bm * (1 + g))
    future_months = [m for m in window_months if m >= today.month] or list(window_months)
    hot_end_month = int(rec.hot_end_month or max(window_months))

    # 库存覆盖天数：与节日主口径同源（去年各月 × (1+G) → 按各月天数逐月扣减可用库存）
    coverage = _window_coverage(
        inventory,
        {"monthly_sales": [{"month": k, "qty": v} for k, v in sorted(last_year_by_month.items())],
         "trend_coeff": 1 + g},
        win_start, win_end,
    )

    return {
        "window_months": window_months,
        "festival_year": festival_year,
        "baseline": baseline,
        "last_year_by_month": last_year_by_month,
        "this_year_30d": this_year_30d,
        "last_year_30d": last_year_30d,
        "g": g,
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
            reason = (f"节日窗口[{window_label}]预估总需求{est}（{int((festival_window.get('g') or 0)*100)}%同期增长），"
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


_PID_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "p_id"
)


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
                stage_end=None, is_new_product: bool = False) -> dict:
    """计算采购评分（新老品两套逻辑）

    新品（is_new_product=True）：沿用原六维线性评分 + SCORE_BALANCE_* 权重（默认1.0均分）。
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
        profit_score = min(100, max(0, round(profit_rate * 100)))
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


