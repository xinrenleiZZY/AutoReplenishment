# -*- coding: utf-8 -*-
"""节日时间点表 → 生命周期判定（优先口径）

规则：
  1. 产品节日 = listing 标签映射（tag_festival）；缺少时用核心销售月份补充为季节类；
  2. 生命周期 = festival_calendar 中该节日的 启动期/增长期/热卖期/成熟期/下降期
     「月份+旬」区间 + 当前日期判定；当前日期不在任何阶段区间 → 返回 None。

区间支持格式：
  7月 / 8月-9月上旬 / 9月中旬-10月上旬 / 10月中旬 / 11-12月 / 4-5月 /
  1月上旬-中旬 / 12月中旬-1月上旬（跨年）/ 12月21-25日 / 6月6-16日 /
  12月26-12月31日 / 5月下旬-6月5日
"""
import re
import calendar
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.festival_calendar import FestivalCalendar
from app.models.festival_lifecycle_days import FestivalLifecycleDays

LIFECYCLE_STAGES = ["启动期", "增长期", "热卖期", "成熟期", "下降期"]
STAGE_FIELDS = {
    "启动期": "launch_stage",
    "增长期": "growth_stage",
    "热卖期": "hot_period",
    "成熟期": "mature_stage",
    "下降期": "decline_stage",
}

_XUN_DAYS = {"上": (1, 10), "中": (11, 20), "下": (21, 31)}


def _parse_side(side: str, inherit_month: int | None = None) -> tuple[int, int, int] | None:
    """解析区间一侧 → (month, day_start, day_end)；无月份时继承 inherit_month"""
    side = side.strip()
    if not side:
        return None
    m = re.match(r"^(\d{1,2})月?(上|中|下)旬$", side)
    if m:
        ds, de = _XUN_DAYS[m.group(2)]
        return int(m.group(1)), ds, de
    m = re.match(r"^(\d{1,2})月(\d{1,2})日?$", side)
    if m:
        d = int(m.group(2))
        return int(m.group(1)), d, d
    m = re.match(r"^(\d{1,2})月$", side)
    if m:
        return int(m.group(1)), 1, 31
    m = re.match(r"^(上|中|下)旬$", side)
    if m and inherit_month:
        ds, de = _XUN_DAYS[m.group(1)]
        return inherit_month, ds, de
    m = re.match(r"^(\d{1,2})日$", side)
    if m and inherit_month:
        d = int(m.group(1))
        return inherit_month, d, d
    if side.isdigit():
        return int(side), 1, 31
    return None


def parse_stage(stage: str | None) -> list[tuple[int, int, int, int]]:
    """解析阶段区间字符串 → [(start_month, start_day, end_month, end_day), ...]"""
    if not stage:
        return []
    s = str(stage).strip().replace(" ", "")
    if not s or s in ("-", "None") or (s.isdigit() and len(s) >= 4):
        return []  # 空 / Excel 序列号等无效值

    # 同月日区间：12月21-25日 / 6月6-16日 / 2月1-6日
    m = re.match(r"^(\d{1,2})月(\d{1,2})-(\d{1,2})日$", s)
    if m:
        mo, d1, d2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return [(mo, d1, mo, d2)]
    # 跨月带日：12月26-12月31日 / 5月下旬-6月5日
    m = re.match(r"^(\d{1,2})月(\d{1,2})日?-(\d{1,2})月(\d{1,2})日?$", s)
    if m:
        return [(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))]

    # 旬/月区间：8月-9月上旬 / 9月中旬-10月上旬 / 11-12月 / 1月上旬-中旬 / 12月中旬-1月上旬
    if "-" in s:
        left, right = s.split("-", 1)
        lm = re.match(r"^(\d{1,2})月", left)
        start = _parse_side(left)
        if start is None:
            return []
        inherit = start[0] if lm else None
        end = _parse_side(right, inherit)
        if end is None:
            return []
        return [(start[0], start[1], end[0], end[2])]

    # 单段：7月 / 10月中旬
    one = _parse_side(s)
    if one:
        return [(one[0], one[1], one[0], one[2])]
    return []


def _in_range(cur_m: int, cur_d: int, start_m: int, start_d: int, end_m: int, end_d: int) -> bool:
    cur = (cur_m, cur_d)
    start = (start_m, start_d)
    end = (end_m, end_d)
    if start <= end:
        return start <= cur <= end
    # 跨年区间（如 12月中旬-1月上旬）
    return cur >= start or cur <= end


def _abs_date(year: int, month: int, day: int) -> date:
    """按年构造日期（月天数越界时收敛到月末，如 2月31日→2月28日）"""
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def lifecycle_stage_days(stage: str | None, year: int) -> int:
    """把阶段区间字符串换算成该年份的实际天数（用于等级评定的各周期天数）

    非跨年区间：2月 → 2月28/29天；8月-9月上旬 → 8月全月 + 9月前10天；
    跨年区间：12月中旬-1月上旬 → 当年12月11-31 + 次年1月1-10 = 31天。
    命中月份天数为实际自然天数（闰年2月29天）。
    区间为空/无效 → 0。
    """
    total = 0
    for start_m, start_d, end_m, end_d in parse_stage(stage):
        if start_m <= end_m:
            start = _abs_date(year, start_m, start_d)
            end = _abs_date(year, end_m, end_d)
            total += (end - start).days + 1
        else:
            # 跨年区间：当年末段 + 次年头段（如 12月中旬-1月上旬）
            start = _abs_date(year, start_m, start_d)
            total += (date(year, 12, 31) - start).days + 1
            total += (_abs_date(year + 1, end_m, end_d) - date(year + 1, 1, 1)).days + 1
    return total


async def stage_days_by_festival(festival: str, year: int, session: AsyncSession) -> dict[str, int] | None:
    """读取缓存表 festival_lifecycle_days 中该节日指定年份的各阶段实际天数

    返回 {"启动期": N, "增长期": N, "热卖期": N, "成熟期": N, "下降期": N}；
    无缓存记录返回 None（调用方按「等级空着，不计算」处理）。
    该表由 scripts/compute_festival_lifecycle_days.py 回填，等级评定直接调用，不在运行时实时换算。
    """
    result = await session.execute(
        select(FestivalLifecycleDays).where(
            FestivalLifecycleDays.festival == festival,
            FestivalLifecycleDays.year == year,
        ).limit(1)
    )
    rec = result.scalar_one_or_none()
    if rec is None:
        return None
    return {
        "启动期": rec.launch_stage or 0,
        "增长期": rec.growth_stage or 0,
        "热卖期": rec.hot_period or 0,
        "成熟期": rec.mature_stage or 0,
        "下降期": rec.decline_stage or 0,
    }


def _pick_nearest(records: list, today: date):
    """多记录时选节日日期/上架时间/结束时间离当前最近的一条"""
    best = records[0]
    best_gap = None
    for r in records:
        target = r.festival_date or r.listing_start or r.festival_end
        if target is None:
            continue
        gap = abs((target.date() - today).days)
        if best_gap is None or gap < best_gap:
            best_gap = gap
            best = r
    return best


# 核心销售月份 → 季节类补充（listing 标签缺节日时使用）
SEASON_MONTHS = {
    "春季类": {3, 4, 5},
    "夏季类": {6, 7, 8},
    "秋季类": {9, 10, 11},
    "冬季类": {12, 1, 2},
}

# 产品节日名 → festival_calendar 表内实际节日名（表名可能带品牌前缀/后缀）
FESTIVAL_ALIASES = {
    "基督教主题": "Christian基督教主题",
    "茶会": "茶会主题",
}


async def match_festival_name(festival: str, session: AsyncSession) -> str | None:
    """节日名 → festival_calendar 表内实际节日名（精确 → 别名 → 子串包含）

    返回 None 表示表中无该节日（无时间点数据，调用方按"无时间限制"处理）。
    """
    if not festival:
        return None
    # 精确匹配
    r = await session.execute(
        select(FestivalCalendar.festival).where(FestivalCalendar.festival == festival).limit(1)
    )
    name = r.scalar_one_or_none()
    if name:
        return name
    # 显式别名（如 基督教主题 → Christian基督教主题）
    alias = FESTIVAL_ALIASES.get(festival)
    if alias:
        r = await session.execute(
            select(FestivalCalendar.festival).where(FestivalCalendar.festival == alias).limit(1)
        )
        name = r.scalar_one_or_none()
        if name:
            return name
    # 子串包含（产品名是表名的一部分或反之）
    rows = (await session.execute(select(FestivalCalendar.festival))).scalars().all()
    for n in rows:
        if festival in n or n in festival:
            return n
    return None


def festival_from_core_months(core_months: str | None) -> str | None:
    """核心销售月份（如 '6,7,5'）→ 季节类节日；无法判断返回 None"""
    if not core_months:
        return None
    months = set()
    for part in str(core_months).split(","):
        try:
            months.add(int(part))
        except (ValueError, TypeError):
            continue
    if not months:
        return None
    best, best_hit = None, 0
    for season, sm in SEASON_MONTHS.items():
        hit = len(months & sm)
        if hit > best_hit:
            best, best_hit = season, hit
    return best if best_hit >= max(1, len(months) // 2) else None


async def resolve_festival(product, session: AsyncSession) -> str | None:
    """产品节日：listing 标签优先；缺少时用核心销售月份补充为季节类

    长期产品（product_type=长期产品，如标签含「长期/西部牛仔」）全年销售不过季，
    不参与任何节日生命周期/季节判断 → 直接返回 None。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return None
    festival = (getattr(product, "festival", "") or "").strip()
    if festival:
        return festival
    return festival_from_core_months(getattr(product, "core_months", None))


async def lifecycle_by_festival(product, session: AsyncSession, current_date: date | None = None) -> str | None:
    """按「节日时间点表 + 当前时间」判定生命周期；无节日/记录/匹配不到返回 None"""
    festival = await resolve_festival(product, session)
    if not festival:
        return None
    today = current_date or date.today()
    name = await match_festival_name(festival, session)
    if not name:
        return None
    records = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    if not records:
        return None
    rec = _pick_nearest(list(records), today)
    for stage in LIFECYCLE_STAGES:
        raw = getattr(rec, STAGE_FIELDS[stage], None)
        for start_m, start_d, end_m, end_d in parse_stage(raw):
            if _in_range(today.month, today.day, start_m, start_d, end_m, end_d):
                return stage
    return None


# 未来 N 月销量预测「不截断」的除外节日：即使生命周期结束落在预测窗口内，仍按完整月数预测
FORECAST_EXEMPT_FESTIVALS = {
    "长期产品", "感恩节", "圣诞节", "秋季类", "冬季类", "农历新年", "跨年", "情人节",
}


async def lifecycle_end_date(product, session: AsyncSession, current_date: date | None = None) -> date | None:
    """节日生命周期整体结束日期（取最后一个有效阶段，通常为「下降期」的结束日）。

    截断日以预设节日结束时间 festival_calendar.festival_end 为准；festival_end 缺失、
    年份不可信（与锚点相差超过 1 年）或早于 today 时，回退到阶段结束日。

    用于未来 N 月销量预测：若该结束日期落在预测窗口内，则只需预测到结束日。
    以下情形返回 None（表示不做截断，仍按完整月数预测）：
      - 长期产品，或解析不到节日 / 节日不在 festival_calendar；
      - 除外节日：长期产品、感恩节、圣诞节、秋季类（秋）、冬季类（冬）、
        农历新年、跨年、情人节等；
      - 结束日期早于 today（生命周期已整体结束，交由其他规则处理）。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return None
    festival = await resolve_festival(product, session)
    if not festival or festival in FORECAST_EXEMPT_FESTIVALS:
        return None
    today = current_date or date.today()
    name = await match_festival_name(festival, session)
    if not name or name in FORECAST_EXEMPT_FESTIVALS:
        return None
    records = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    if not records:
        return None
    rec = _pick_nearest(list(records), today)
    anchor = rec.festival_date or rec.listing_start or rec.festival_end
    anchor_date = anchor.date() if hasattr(anchor, "date") else (anchor or today)
    # 从最后一个阶段往前找首个有效区间，其结束日即生命周期结束日
    for stage in reversed(LIFECYCLE_STAGES):
        parsed = parse_stage(getattr(rec, STAGE_FIELDS[stage], None))
        if not parsed:
            continue
        _sm, _sd, end_m, end_d = parsed[-1]
        candidates = [_abs_date(anchor_date.year + dy, end_m, end_d) for dy in (-1, 0, 1)]
        end_date = min(candidates, key=lambda d: abs((d - anchor_date).days))
        # 预设节日结束时间（festival_calendar.festival_end）为优先口径：年份需与锚点相差 1 年内
        # （部分记录 festival_end 为历史年份如 2019-xx）、且不早于 today，否则回退阶段结束日
        preset_end = getattr(rec, "festival_end", None)
        if preset_end is not None:
            preset_end = preset_end.date() if hasattr(preset_end, "date") else preset_end
            if abs(preset_end.year - anchor_date.year) <= 1 and preset_end >= today:
                end_date = preset_end
        return end_date if end_date >= today else None
    return None


async def lifecycle_stage_end(product, session: AsyncSession, current_date: date | None = None):
    """返回当前命中生命周期的 (stage, 销售窗口结束日期)；未命中/无节日返回 None

    用于「到货日期是否超过生命周期」判断：到货日期 > 结束日期 → 不采购。

    结束日期以预设节日结束时间 festival_calendar.festival_end 为优先口径；
    festival_end 缺失、年份不可信（与锚点相差超过 1 年）或早于 today 时，
    回退到当前命中阶段的结束日期。
    """
    festival = await resolve_festival(product, session)
    if not festival:
        return None
    today = current_date or date.today()
    name = await match_festival_name(festival, session)
    if not name:
        return None
    records = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    if not records:
        return None
    rec = _pick_nearest(list(records), today)
    for stage in LIFECYCLE_STAGES:
        ends = []
        for start_m, start_d, end_m, end_d in parse_stage(getattr(rec, STAGE_FIELDS[stage], None)):
            if _in_range(today.month, today.day, start_m, start_d, end_m, end_d):
                end_date = _abs_date(today.year, end_m, end_d)
                if end_date < today:  # 跨年区间（如 12月中旬-1月上旬），结束在次年
                    end_date = _abs_date(today.year + 1, end_m, end_d)
                ends.append(end_date)
        if ends:
            end_date = max(ends)
            # 预设节日结束时间（festival_calendar.festival_end）为优先口径：
            # 年份需与锚点相差 1 年内（部分记录 festival_end 为历史年份）、且不早于 today
            anchor = rec.festival_date or rec.listing_start or rec.festival_end
            anchor_date = anchor.date() if hasattr(anchor, "date") else (anchor or today)
            preset_end = getattr(rec, "festival_end", None)
            if preset_end is not None:
                preset_end = preset_end.date() if hasattr(preset_end, "date") else preset_end
                if abs(preset_end.year - anchor_date.year) <= 1 and preset_end >= today:
                    end_date = preset_end
            return stage, end_date
    return None


async def launch_stage_start(product, session: AsyncSession, current_date: date | None = None) -> date | None:
    """节日产品「启动期」起始日（生命周期起点，启动期指数的起算点）

    经过天数 = 计算日 − 启动期起始日，供启动期指数 1.03^(经过天数÷10) 使用。
    取 festival_calendar 中该节日「启动期」区间的起始月日，年份锚定 festival_date
    （缺失时按 listing_start / festival_end），在锚点年 ±1 内取距锚点最近的一年
    （跨年区间如 12月中旬-1月上旬 亦正确）。

    以下情形返回 None（视为无启动期）：
      - 长期产品 / 解析不到节日 / 表中无该节日；
      - 该节日「启动期」字段为空或无法解析。
    """
    festival = await resolve_festival(product, session)
    if not festival:
        return None
    today = current_date or date.today()
    name = await match_festival_name(festival, session)
    if not name:
        return None
    records = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    if not records:
        return None
    rec = _pick_nearest(list(records), today)
    parsed = parse_stage(getattr(rec, STAGE_FIELDS["启动期"], None))
    if not parsed:
        return None
    start_m, start_d = parsed[0][0], parsed[0][1]
    anchor = rec.festival_date or rec.listing_start or rec.festival_end
    anchor_date = anchor.date() if hasattr(anchor, "date") else (anchor or today)
    candidates = [_abs_date(anchor_date.year + dy, start_m, start_d) for dy in (-1, 0, 1)]
    return min(candidates, key=lambda d: abs((d - anchor_date).days))


async def next_festival_date(product, session: AsyncSession, current_date: date | None = None):
    """产品「下一次节日到来」日期（festival_calendar.festival_date）

    用于分析前的节日门禁：节日产品的节日时间须落在未来 6 个月内才触发分析。

    返回 (匹配到的表内节日名, 判定日期)：
      - 长期产品 / festival 为空 → (None, None)，不参与门禁；
      - 表中无该节日、或该节日无 festival_date → (节日名或 None, None)，不参与门禁；
      - festival_date 未到（>= today）→ 返回 festival_date；
      - 节日已开始且尚未结束（festival_date <= today <= festival_end）→ 返回 today，
        表示节日正在进行中、视为落在窗口内（如 秋季类 8/1-11/30、意识月）；
      - 节日已结束（festival_end < today）→ 顺延到下一次同月日（年份后推）。
    """
    if (getattr(product, "product_type", "") or "") == "长期产品":
        return None, None
    festival = (getattr(product, "festival", "") or "").strip()
    if not festival:
        return None, None
    today = current_date or date.today()
    name = await match_festival_name(festival, session)
    if not name:
        return None, None
    records = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.festival == name)
    )).scalars().all()
    best: date | None = None
    for rec in records:
        d = getattr(rec, "festival_date", None)
        if d is None:
            continue
        d = d.date() if hasattr(d, "date") else d
        if d >= today:
            cand = d
        else:
            end = getattr(rec, "festival_end", None)
            end = end.date() if hasattr(end, "date") else end
            if end is not None and end >= today:
                cand = today  # 节日已开始尚未结束 → 视为窗口内
            else:
                cand = d
                guard = 0
                while cand < today and guard < 100:  # 节日已过 → 顺延到下一次同月日
                    cand = _abs_date(cand.year + 1, cand.month, cand.day)
                    guard += 1
        if best is None or cand < best:
            best = cand
    return name, best
