"""预测域（C1 拆分批2 · Phase 3 / B-01）

来源：app/tasks/calculation_tasks.py（2026-10-10 按附录 H 拆出，纯搬运未改逻辑）。
含：日期小工具、历史销量分析、趋势系数（上限 1.5 封顶）、节日/长期窗口、
    预测系数与四类预测（新品/节日老品/长期老品/AI 兜底）。
对外入口不变：app.tasks.calculation_tasks.<name> 仍可用（原文件显式导入本模块）。
"""

import calendar
import logging
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.product import Product
from app.models.sales import SalesData
from app.services import ai_eval, new_product_policy
from app.services.forecast import (
    compute_ad_coeff,
    compute_listing_coeff,
    compute_market_coeff,
    compute_trend_coeff,
    forecast_all_months,
    month_lifecycle_ratio,
)
from app.services.sales_fallback import get_daily_sales_dual, sum_daily_sales_dual
from app.services.semantic_classify import get_semantic_classification
from app.services.time_axis import get_festival_info, resolve_lead_time
from app.services.calc.common import (
    _get_new_product_cfg,
    _recent_daily_orders,
    _safe_int,
    _to_float_safe,
)

logger = logging.getLogger(__name__)

# 趋势系数上限：超过直接用 1.5（封顶），原值用于文字标注
TREND_COEFF_MAX = 1.5


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


def _fmt_trend_coeff(capped, raw, ndigits: int = 2) -> str:
    """趋势系数文字表述：被 1.5 封顶时输出「1.50（原值：X.XX）」，否则只输出数值"""
    if capped is None:
        return "-"
    cap = round(float(capped), ndigits)
    if raw is None:
        return f"{cap:.{ndigits}f}"
    raw_val = round(float(raw), ndigits)
    if raw_val > cap:
        return f"{cap:.{ndigits}f}（原值：{raw_val:.{ndigits}f}）"
    return f"{cap:.{ndigits}f}"


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
        "trend_coeff_raw": fw["trend_coeff_raw"],
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
        "lt_trend_coeff_raw": lt["trend_coeff_raw"],
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
      5. 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0），上限 1.5（超过直接用 1.5）。

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
    trend_coeff_raw = round(recent_30d_qty / last_year_30d_qty, 4) if last_year_30d_qty > 0 else 1.0
    # 最终趋势系数口径：上限 1.5（超过直接用 1.5，原值另存便于文字表述）
    trend_coeff = round(min(trend_coeff_raw, TREND_COEFF_MAX), 4)

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
        "trend_coeff_raw": trend_coeff_raw,
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
      4. 趋势系数 = 最近30天销量 ÷ 去年同期30天销量（去年为 0 → 1.0），上限 1.5（超过直接用 1.5）；
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
    trend_coeff_raw = round(recent_30d_qty / last_year_30d_qty, 4) if last_year_30d_qty > 0 else 1.0
    # 最终趋势系数口径：上限 1.5（超过直接用 1.5，原值另存便于文字表述）
    trend_coeff = round(min(trend_coeff_raw, TREND_COEFF_MAX), 4)

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
        "trend_coeff_raw": trend_coeff_raw,
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

    - 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0，已在 Step 4 兜底并封顶 1.5）；
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

    - 趋势系数 = 今年近30天销量 ÷ 去年同30天销量（去年为 0 → 1.0，已在 Step 4 兜底并封顶 1.5）；
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


