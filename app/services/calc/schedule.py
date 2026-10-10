"""计算调度配置与到期判定（C1 拆分批4 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
含：等级/频率/生命周期配置装载、计算时间轴重置、到期判定。
常量 _P_LEVEL_MAP / _ALL_LEVELS / LEVEL_3TIER 随本模块迁移。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.calculation import CalculationResult, CalculationTimelineReset
from app.models.product import Product

logger = logging.getLogger(__name__)

# 频率配置解析缓存（原 calculation_tasks 模块级变量，随本模块迁移）
_FREQ_CACHE: dict | None = None


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


def normalize_level(level) -> str | None:
    """级别列统一为三档：80-100立即采购 / 60-79观察 / 1-59暂停（及无评分决策映射）"""
    return LEVEL_3TIER.get(level or "", level)


# ── 常量（随本模块迁移）──


_P_LEVEL_MAP = {"P0": "S", "P1": "A", "P2": "B", "P3": "C", "P4": "D"}


_ALL_LEVELS = ("S", "A", "B", "C", "D")


LEVEL_3TIER = {
    "立即采购": "立即采购",
    "建议采购": "立即采购",
    "观察": "观察",
    "暂停": "暂停",
    "未触发": "暂停",
    "终止": "暂停",
}


