"""产品等级自动分级脚本

口径（可切换）：
  方案D mixed（默认）：
    老品 → 去年总销量（sales_data / 领星月度 / 销售统计报表）；
    新品 → 节日生命周期年预测 = 基准(近3天日均) × Σ(各生命周期预测销量系数 × 阶段天数 × 安全系数)；
          仅计算实际生命周期时间区间（festival_lifecycle_days 缓存表），非节日新品等级空着。
  方案C annualize：全部按近30天年化（旧口径，保留可随时切换）
阈值（需求文档）：S≥5000 / A 2000-4999 / B 1000-1999 / C 300-999 / D 1-299

用法:
  python scripts/assign_product_levels.py                 # 默认按配置 product_level_mode
  python scripts/assign_product_levels.py --mode mixed    # 老品去年总销量+新品生命周期年预测
  python scripts/assign_product_levels.py --mode annualize# 方案C（全部近30天年化）
"""
import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

# 项目根目录 = 脚本所在目录的上一级，兼容 docker(/app/scripts) 与本地运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select
from app.database import async_session_factory
from app.models.product import Product
from app.models.daily_snapshot import DailySalesSnapshot
from app.services.lifecycle import _map_sales_to_level, LEVEL_FREQUENCY

# 近30天销量 → 年销量估算（30天 × 12，仅方案C annualize 使用）
ANNUALIZE = 12.0


async def get_latest_snapshot_volumes(session, asins: list) -> dict:
    """从数据库最新快照读取每个 ASIN 的近30天销量"""
    latest = (await session.execute(
        select(DailySalesSnapshot.snapshot_date)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return {}
    rows = (await session.execute(
        select(DailySalesSnapshot.asin, DailySalesSnapshot.thirty_volume)
        .where(DailySalesSnapshot.snapshot_date == latest)
    )).all()
    return {r[0]: (r[1] or 0) for r in rows}


async def _parse_lifecycle_coeff(raw: str | None) -> dict[str, tuple]:
    """把 config_service 的 lifecycle_coeff（JSON字符串）解析为 {阶段: (预测系数, 安全系数)}"""
    from app.services.lifecycle import DEFAULT_LIFECYCLE_COEFF

    if not raw:
        return DEFAULT_LIFECYCLE_COEFF
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return DEFAULT_LIFECYCLE_COEFF
    coeffs: dict[str, tuple] = {}
    for stage, pair in data.items():
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            coeffs[stage] = (float(pair[0]), float(pair[1]))
    return coeffs or DEFAULT_LIFECYCLE_COEFF


async def _new_product_forecast(session, product) -> int | None:
    """新品年预测销量（新等级口径）：基准(近N天日均) × Σ(各阶段预测系数 × 阶段天数 × 安全系数)

    长期产品：全年热卖期不过季 → 直接用热卖期 365 天计算。
    其他新品：仅当命中节日（festival_calendar）且缓存表 festival_lifecycle_days 有该节日当年阶段天数时计算；
    非节日新品 / 无法识别节日 / 无基准销量 → 返回 None（等级空着，不计算）。
    基准天数 default 3，各阶段系数 default 见 DEFAULT_LIFECYCLE_COEFF，均可由 config_service 覆盖。
    """
    from app.services.config_service import get_param
    from app.services.festival_lifecycle import (
        resolve_festival, match_festival_name, stage_days_by_festival,
    )
    from app.services.lifecycle import get_base_daily_sales, new_product_annual_forecast

    base_days = await get_param(session, "new_product_base_days") or 3
    base = await get_base_daily_sales(session, product.asin, days=base_days)
    if base is None:
        return None
    coeffs = await _parse_lifecycle_coeff(await get_param(session, "lifecycle_coeff"))

    if (getattr(product, "product_type", "") or "") == "长期产品":
        # 全年销售不过季，等级 = 基准 × 365 × 安全系数（安全系数参数表可配置，默认 0.5）
        safety = await get_param(session, "long_term_level_safety_factor") or 0.5
        return round(base * 365 * float(safety))

    festival = await resolve_festival(product, session)
    if not festival:
        return None
    name = await match_festival_name(festival, session)
    if not name:
        return None
    year = date.today().year
    stage_days = await stage_days_by_festival(name, year, session)
    if not stage_days:
        return None
    return new_product_annual_forecast(base, stage_days, coeffs)


async def run_assign_levels(session, mode: str | None = None, dry_run: bool = False, limit: int = 0) -> dict:
    """全量/部分产品分级：老品 → 去年总销量；新品 → 节日生命周期年预测（非节日新品等级空着）。
    写回 product_stage（新老品）、product_level、calc_frequency。返回等级分布。"""
    from app.services.config_service import get_param
    from app.services.product_stage import is_new_product_basic

    if mode is None:
        mode = (await get_param(session, "product_level_mode")) or "mixed"
    if mode == "mixed":
        print("分级口径: mixed（老品=去年总销量；新品=节日生命周期年预测(基准近3天日均×各阶段系数×安全系数)，非节日新品等级空着）")
    else:
        print("分级口径: annualize（全部近30天年化，旧口径）")
    q = select(Product).where(Product.status == True)  # noqa: E712
    if limit:
        q = q.limit(limit)
    products = (await session.execute(q)).scalars().all()
    print(f"共 {len(products)} 个启用产品，从数据库快照读取销量...")

    # 近30天销量年化（新品 / 方案C）
    asins = [p.asin for p in products]
    thirty = await get_latest_snapshot_volumes(session, asins)
    annualized = {a: round(thirty.get(a, 0) * ANNUALIZE) for a in asins}
    new_keywords = (await get_param(session, "new_product_name_keywords")) or "26版,27版"

    # 去年总销量（老品 / 方案D）
    last_year_sales = {}
    lx_last_year_sales = {}
    stat_last_year_sales = {}
    if mode == "mixed":
        from app.models.sales import SalesData
        from app.models.historical_monthly import HistoricalMonthlyStats
        from app.models.sales_statistics import SalesStatisticsReport
        from sqlalchemy import func

        last_year = date.today().year - 1
        rows = (await session.execute(
            select(SalesData.asin, func.coalesce(func.sum(SalesData.sales_qty), 0))
            .where(func.extract("year", SalesData.date) == last_year)
            .group_by(SalesData.asin)
        )).all()
        last_year_sales = {r[0]: int(r[1] or 0) for r in rows}
        lx_rows = (await session.execute(
            select(HistoricalMonthlyStats.asin, func.coalesce(func.sum(HistoricalMonthlyStats.sale_quantity), 0))
            .where(
                HistoricalMonthlyStats.source == "lingxing",
                HistoricalMonthlyStats.month.like(f"{last_year}-%"),
            )
            .group_by(HistoricalMonthlyStats.asin)
        )).all()
        lx_last_year_sales = {r[0]: int(r[1] or 0) for r in lx_rows}
        # 新数据源：领星销售统计报表（sales-statistics/report/list）
        stat_rows = (await session.execute(
            select(SalesStatisticsReport.asin, SalesStatisticsReport.total_value,
                   SalesStatisticsReport.trend_data_json)
            .where(
                SalesStatisticsReport.stat_start_date == f"{last_year}-01-01",
                SalesStatisticsReport.stat_end_date == f"{last_year}-12-31",
            )
        )).all()
        for a, tv, td in stat_rows:
            val = None
            if td:
                try:
                    tdj = json.loads(td)
                    val = ((tdj.get("main") or {}).get(str(last_year)) or {}).get("value")
                except Exception:  # noqa: BLE001
                    val = None
            if val in (None, ""):
                val = tv
            try:
                stat_last_year_sales[a] = int(float(val or 0))
            except (ValueError, TypeError):
                stat_last_year_sales[a] = 0
        print(f"去年({last_year})总销量覆盖: 销售统计 {len(stat_last_year_sales)} 个 ASIN / "
              f"领星月度 {len(lx_last_year_sales)} / sales_data {len(last_year_sales)}")

    stats = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
    skipped = 0  # 非节日新品 / 无基准，等级空着
    final_vol: dict = {}
    for p in products:
        annual = annualized.get(p.asin, 0)
        is_new = is_new_product_basic(p, new_keywords)
        if mode == "mixed":
            if not is_new:
                vol = (stat_last_year_sales.get(p.asin, 0)
                       or lx_last_year_sales.get(p.asin, 0)
                       or last_year_sales.get(p.asin, 0))
            else:
                vol = await _new_product_forecast(session, p)
        else:
            vol = annual
        final_vol[p.asin] = vol
        if vol is None:
            level = ""
            freq = None
            skipped += 1
        else:
            level = _map_sales_to_level(vol)
            freq = LEVEL_FREQUENCY.get(level, "P4")
            stats[level] = stats.get(level, 0) + 1
        if not dry_run:
            p.product_stage = "新品" if is_new else "老品"
            p.product_level = level if level else None
            p.calc_frequency = freq
    if not dry_run:
        await session.commit()
        print("已写回 product_stage（新老品）/ product_level / calc_frequency")

    print("\n等级分布:", stats, f"（{skipped} 个新品等级空着不计算）")
    s_list = [(p, final_vol[p.asin]) for p in products
              if isinstance(final_vol.get(p.asin), int) and final_vol[p.asin] >= 5000]
    s_list.sort(key=lambda x: -x[1])
    print(f"\nS级产品 {len(s_list)} 个，TOP10:")
    for p, vol in s_list[:10]:
        stage = "新品" if (p.list_date and (date.today() - p.list_date).days <= 365) else "老品"
        print(f"  {p.asin} | {p.product_name[:45]} | 销量={vol} | {stage} | list={p.list_date}")
    return stats


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前N个产品(0=全部)")
    parser.add_argument("--dry-run", action="store_true", help="只展示不写库")
    parser.add_argument("--mode", type=str, choices=["mixed", "annualize"], default=None,
                        help="分级口径：mixed=方案D / annualize=方案C（不传则读配置 product_level_mode）")
    args = parser.parse_args()

    async def _run():
        async with async_session_factory() as s:
            await run_assign_levels(s, args.mode, args.dry_run, args.limit)

    asyncio.run(_run())


if __name__ == "__main__":
    main_cli()
