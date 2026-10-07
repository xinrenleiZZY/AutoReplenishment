# -*- coding: utf-8 -*-
"""领星逐日销量表抓取脚本（sales-statistics/report/list，filterDateType=day）

数据源：领星网页 API https://gw.lingxingerp.com/sales-statistics/report/list
功能：统计区间内每个 ASIN 的单日销量，逐日落库到 daily_sales_stats 表。
  - filterDateType=day 时每个 ASIN 返回一条聚合记录，其 trend_data.main 为
    {日期: {value: 单日销量}} 的逐日字典，一次请求即可拿到整段区间所有单日销量。
  - 只保留系统跟踪的产品（products.status=True）。
  - 幂等：同一 (asin, stat_date) 只保留最新一次抓取（区间删旧写新）。

用法:
    python scripts/sync_daily_sales.py --backfill                # 首次全量回填(去年1/1~今天)
    python scripts/sync_daily_sales.py --daily                   # 每日增量(近7天)
    python scripts/sync_daily_sales.py --start 2025-01-01 --end 2026-09-01
"""

import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from sqlalchemy import delete, func, select

from app.database import async_session_factory
from app.models.daily_sales_stat import DailySalesStat
from app.models.product import Product
from scripts.sync_sales_statistics import fetch_all

SEQ_START = 100  # req_time_sequence 序号，避免与报表接口冲突


def extract_daily(item: dict) -> tuple[str | None, dict]:
    """从单条记录提取 (asin, {日期: 单日销量})；无 asin 或 trend_data.main 为空则返回 (None, {})"""
    asin_list = item.get("asin")
    asin = None
    if isinstance(asin_list, list) and asin_list:
        asin = (asin_list[0] or {}).get("asin")
    if not asin:
        return None, {}
    td = item.get("trend_data")
    main = td.get("main") if isinstance(td, dict) else None
    if not isinstance(main, dict):
        return asin, {}
    daily = {}
    for dstr, v in main.items():
        if not isinstance(v, dict):
            continue
        val = v.get("value")
        try:
            daily[dstr] = int(float(str(val))) if val not in (None, "") else 0
        except (TypeError, ValueError):
            daily[dstr] = 0
    return asin, daily


async def collect_daily(start: str, end: str, product_asins: set) -> dict:
    """抓取区间内所有 ASIN 单日销量（服务站一次拉全量），仅保留 product_asins 中的 ASIN"""
    seq = SEQ_START
    result: dict = {}
    lst, _total = await asyncio.to_thread(fetch_all, start, end, "volume", "asin", "day", seq)
    for item in lst:
        if not isinstance(item, dict):
            continue
        asin, daily = extract_daily(item)
        if asin not in product_asins:
            continue
        result.setdefault(asin, {}).update(daily)
    return result


def _parse_date(s: str):
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


async def write_daily(start, end, asin_daily: dict, scope_asins: set | None = None) -> int:
    """逐日落库：删除区间内旧行后全量写入（幂等）。start/end 为 date。返回写入行数

    scope_asins: 只删除并重写这些 ASIN 的行（None=区间内全量）。用于单ASIN回填，避免误删其他ASIN历史。
    """
    if not asin_daily:
        return 0
    session = async_session_factory()
    added = 0
    try:
        async with session:
            stmt = delete(DailySalesStat).where(
                DailySalesStat.stat_date >= start,
                DailySalesStat.stat_date <= end,
            )
            if scope_asins:
                stmt = stmt.where(DailySalesStat.asin.in_(scope_asins))
            await session.execute(stmt)
            rows = []
            for asin, daily in asin_daily.items():
                for dstr, qty in daily.items():
                    d = _parse_date(dstr)
                    if d is None:
                        continue
                    rows.append(DailySalesStat(asin=asin, stat_date=d, sales_qty=qty, data_source="lx"))
            # 分批写入，避免大区间一次性 add_all 占用过多内存/事务
            BATCH = 20000
            for i in range(0, len(rows), BATCH):
                session.add_all(rows[i:i + BATCH])
                await session.flush()
                added += len(rows[i:i + BATCH])
            await session.commit()
    finally:
        await session.close()
    return added


async def get_product_asins() -> set:
    """系统跟踪产品 ASIN 集合（products.status=True）"""
    session = async_session_factory()
    try:
        async with session:
            rows = (await session.execute(
                select(Product.asin).where(Product.status == True)  # noqa: E712
            )).scalars().all()
        return set(rows)
    finally:
        await session.close()


async def needs_backfill() -> bool:
    """表为空或最早数据晚于近30天前 → 尚未全量回填历史，需触发首次回填"""
    session = async_session_factory()
    try:
        async with session:
            earliest = (await session.execute(
                select(func.min(DailySalesStat.stat_date))
            )).scalar()
        if earliest is None:
            return True
        return earliest > (date.today() - timedelta(days=30))
    finally:
        await session.close()


async def backfill() -> dict:
    """首次全量回填：去年1月1日 ~ 今天"""
    today = date.today()
    start = date(today.year - 1, 1, 1)
    end = today
    asins = await get_product_asins()
    print(f"[backfill] {start} ~ {end}, 跟踪ASIN数: {len(asins)}")
    daily = await collect_daily(start.isoformat(), end.isoformat(), asins)
    written = await write_daily(start, end, daily)
    print(f"[backfill] 写入 {written} 行")
    return {"start": start.isoformat(), "end": end.isoformat(), "asins": len(asins), "written": written}


async def daily_update() -> dict:
    """每日增量：今天往前推 7 天（近7天），覆盖平台延迟结算/回溯改数"""
    today = date.today()
    start = today - timedelta(days=6)
    end = today
    asins = await get_product_asins()
    print(f"[daily] {start} ~ {end}, 跟踪ASIN数: {len(asins)}")
    daily = await collect_daily(start.isoformat(), end.isoformat(), asins)
    written = await write_daily(start, end, daily, scope_asins=asins)
    print(f"[daily] 写入 {written} 行")
    return {"start": start.isoformat(), "end": end.isoformat(), "asins": len(asins), "written": written}


async def manual_update(start: str, end: str, asins: set | None = None) -> dict:
    """手动区间抓取：跟踪ASIN集合 → 抓取区间逐日销量 → 落库（单事件循环内完成）

    asins: 指定ASIN集合（None=全部跟踪产品）。传入时只回填这些ASIN，且只删除这些ASIN的历史行。
    """
    if asins is None:
        asins = await get_product_asins()
    print(f"[manual] {start} ~ {end}, 跟踪ASIN数: {len(asins)}")
    daily = await collect_daily(start, end, asins)
    written = await write_daily(date.fromisoformat(start), date.fromisoformat(end), daily, scope_asins=asins)
    return {"start": start, "end": end, "asins": len(asins), "written": written}


def main_cli():
    parser = argparse.ArgumentParser(description="领星逐日销量抓取")
    parser.add_argument("--backfill", action="store_true", help="首次全量回填(去年1/1~今天)")
    parser.add_argument("--daily", action="store_true", help="每日增量(近7天)")
    parser.add_argument("--start", type=str, default="", help="统计开始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default="", help="统计结束日期 YYYY-MM-DD")
    parser.add_argument("--asin", type=str, default="", help="只回填指定ASIN(可选，其余按跟踪产品全量)")
    args = parser.parse_args()

    if args.backfill:
        stats = asyncio.run(backfill())
    elif args.daily:
        stats = asyncio.run(daily_update())
    elif args.start and args.end:
        asins = {args.asin} if args.asin else None
        stats = asyncio.run(manual_update(args.start, args.end, asins))
    else:
        # 默认：表空则回填，否则近7天增量
        if asyncio.run(needs_backfill()):
            print("表未回填历史，执行首次全量回填")
            stats = asyncio.run(backfill())
        else:
            print("已回填历史，执行近7天增量")
            stats = asyncio.run(daily_update())

    print(f"\n结果: {stats}")


if __name__ == "__main__":
    main_cli()
