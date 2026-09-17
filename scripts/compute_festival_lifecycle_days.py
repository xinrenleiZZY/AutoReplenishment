# -*- coding: utf-8 -*-
"""节日生命周期天数回填脚本 - 预计算每个节日在指定年份的各生命周期阶段实际天数

背景：等级评定（run_assign_levels 新品支）需要「各周期天数」，其区间来自 festival_calendar
（如 启动期=2月 → 今年2月28/29天）。为避免运行时反复换算，先把各节日各年份的阶段天数算好入库
festival_lifecycle_days，等级评定直接读取缓存表。

规则：
  1. 阶段区间解析复用 app/services/festival_lifecycle.py 的 parse_stage + lifecycle_stage_days；
  2. 跨年区间（如 12月中旬-1月上旬）：当年12月11-31 + 次年1月1-10 = 31天；
  3. 同一节日多条记录时，取阶段字段最完整的一条；
  4. 年份默认计算「今年 + 明年」（跨年区间安全），可用 --years 调整。

用法:
  python scripts/compute_festival_lifecycle_days.py                  # 今年+明年
  python scripts/compute_festival_lifecycle_days.py --years 1       # 仅今年
  python scripts/compute_festival_lifecycle_days.py --dry-run       # 只打印不写入
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

# 项目根目录 = 脚本所在目录的上一级，兼容 docker(/app/scripts) 与本地运行
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.festival_calendar import FestivalCalendar
from app.models.festival_lifecycle_days import FestivalLifecycleDays
from app.services.festival_lifecycle import STAGE_FIELDS, lifecycle_stage_days


def _best_record(records: list):
    """同一节日多条记录时，选择阶段字段最完整的一条"""
    def _score(r):
        return sum(1 for f in STAGE_FIELDS.values() if getattr(r, f, None))
    return max(records, key=_score)


async def compute(session, years: int, dry_run: bool = False) -> dict:
    """遍历节日日历，换算各阶段天数并回填缓存表。返回 (节日, 年份) -> 天数字典。"""
    records = (await session.execute(select(FestivalCalendar))).scalars().all()
    by_name: dict = {}
    for r in records:
        if not r.festival:
            continue
        by_name.setdefault(r.festival, []).append(r)

    current_year = date.today().year
    years_list = [current_year + i for i in range(years)]
    totals: dict = {}

    for name, recs in by_name.items():
        rec = _best_record(recs)
        for year in years_list:
            day_row = {}
            for stage, field in STAGE_FIELDS.items():
                day_row[field] = lifecycle_stage_days(getattr(rec, field, None), year)
            totals[(name, year)] = day_row
            desc = ", ".join(f"{k}={v}" for k, v in day_row.items())
            print(f"{name} | {year} | {desc}")
            if dry_run:
                continue
            existing = (await session.execute(
                select(FestivalLifecycleDays).where(
                    FestivalLifecycleDays.festival == name,
                    FestivalLifecycleDays.year == year,
                ).limit(1)
            )).scalar_one_or_none()
            if existing:
                for field, days in day_row.items():
                    setattr(existing, field, days)
            else:
                session.add(FestivalLifecycleDays(festival=name, year=year, **day_row))

    if not dry_run:
        await session.commit()
        print(f"\n已回填 {len(totals)} 条 (节日,年份) 阶段天数记录")
    return totals


def main_cli():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=int, default=2, help="从今年起计算的年份数（默认2=今年+明年；1=仅今年）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不写入")
    args = parser.parse_args()

    async def _run():
        async with async_session_factory() as s:
            await compute(s, args.years, args.dry_run)

    asyncio.run(_run())


if __name__ == "__main__":
    main_cli()
