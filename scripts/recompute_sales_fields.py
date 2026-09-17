# -*- coding: utf-8 -*-
"""由 daily_sales_stats 重算 products 基础销量字段（昨日/7/14/30天销量 + 日均）。

根因：领星 showOnline 接口不返回 seven_volume 字段（import_lingxing_data 读取后为 None，
且更新时 None 会被过滤掉），导致 products.seven_volume 等字段为空。
daily_sales_stats 已按日实抓真实逐日销量（2025-01-01 起，仅系统跟踪产品），这里用它重算覆盖。

口径（用户已确认）：
  - 覆盖策略：全部重算覆盖（非仅补空值）
  - 统计锚点：含今天（窗口含今天当天；今日数据未最终化时按 0 计入，日均按完整天数除）

用法:
    python scripts/recompute_sales_fields.py                  # 重算所有在窗口内有数据的 ASIN
    python scripts/recompute_sales_fields.py --date 2026-09-02   # 指定锚定日（默认今天）
    python scripts/recompute_sales_fields.py --asins B01,B02    # 仅重算指定 ASIN
    python scripts/recompute_sales_fields.py --dry-run          # 只打印统计，不更新数据库
"""

import argparse
import os
import sys
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from sqlalchemy import select

from app.database import async_session_factory
from app.models.daily_sales_stat import DailySalesStat
from app.models.product import Product

# 各窗口天数
DAY_WINDOW = {"yesterday": 1, "seven": 7, "fourteen": 14, "thirty": 30}


def _parse_date(s: str):
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


async def load_window_sales(anchor: date, asins: set | None) -> dict[str, dict[date, int]]:
    """读取 [anchor-29, anchor] 窗口内所有逐日销量，返回 {asin: {日期: 单日销量}}"""
    window_start = anchor - timedelta(days=DAY_WINDOW["thirty"] - 1)
    session = async_session_factory()
    try:
        async with session:
            stmt = select(DailySalesStat.asin, DailySalesStat.stat_date, DailySalesStat.sales_qty).where(
                DailySalesStat.stat_date >= window_start,
                DailySalesStat.stat_date <= anchor,
            )
            if asins:
                stmt = stmt.where(DailySalesStat.asin.in_(asins))
            rows = (await session.execute(stmt)).all()
    finally:
        await session.close()

    result: dict[str, dict[date, int]] = {}
    for asin, d, qty in rows:
        result.setdefault(asin, {})[d] = qty
    return result


def compute_fields(asin_sales: dict[date, int], anchor: date) -> dict:
    """按锚定日（含今天）计算各窗口销量与日均，返回 {字段名: 值}。窗口无数据则返回空 dict。"""
    if not asin_sales:
        return {}

    def _sum(days: int) -> int:
        start = anchor - timedelta(days=days - 1)
        return sum(q for d, q in asin_sales.items() if start <= d <= anchor)

    # 昨日 = 锚定日的前一天（含今天口径下，今天未最终化视为 0）
    yesterday = asin_sales.get(anchor - timedelta(days=1), 0)
    seven = _sum(7)
    fourteen = _sum(14)
    thirty = _sum(30)

    return {
        "yesterday_volume": yesterday,
        "seven_volume": seven,
        "fourteen_volume": fourteen,
        "thirty_volume": thirty,
        "average_seven_volume": round(seven / 7, 2),
        "average_fourteen_volume": round(fourteen / 14, 2),
        "average_thirty_volume": round(thirty / 30, 2),
    }


async def recompute(anchor: date, asins: set | None, dry_run: bool) -> dict:
    window_sales = await load_window_sales(anchor, asins)
    session = async_session_factory()
    updated = 0
    empty = 0
    zero_filled = 0
    try:
        async with session:
            from sqlalchemy import update
            # 第1步：窗口内有逐日数据的 ASIN，用聚合结果覆盖
            for asin, sales in window_sales.items():
                fields = compute_fields(sales, anchor)
                if not fields:
                    empty += 1
                    continue
                if not dry_run:
                    await session.execute(
                        update(Product).where(Product.asin == asin).values(**fields)
                    )
                updated += 1
                if updated % 300 == 0:
                    await session.flush()
            # 第2步：启用的跟踪产品若无任何窗口数据（全年无销量），补 0 而非留空
            stmt = select(Product.asin).where(
                Product.status == True,  # noqa: E712
                Product.seven_volume.is_(None),
            )
            if asins:
                stmt = stmt.where(Product.asin.in_(asins))
            null_asins = (await session.execute(stmt)).scalars().all()
            if null_asins:
                zero_fields = {k: 0 for k in (
                    "yesterday_volume", "seven_volume", "fourteen_volume",
                    "thirty_volume", "average_seven_volume",
                    "average_fourteen_volume", "average_thirty_volume",
                )}
                if not dry_run:
                    await session.execute(
                        update(Product).where(Product.asin.in_(null_asins)).values(**zero_fields)
                    )
                zero_filled = len(null_asins)
            if not dry_run:
                await session.commit()
    finally:
        await session.close()

    return {
        "anchor": anchor.isoformat(),
        "asins_in_window": len(window_sales),
        "updated": updated,
        "skipped_empty": empty,
        "zero_filled": zero_filled,
        "dry_run": dry_run,
    }


def main_cli():
    parser = argparse.ArgumentParser(description="由 daily_sales_stats 重算 products 基础销量字段")
    parser.add_argument("--date", type=str, default="", help="锚定日 YYYY-MM-DD，默认今天")
    parser.add_argument("--asins", type=str, default="", help="仅重算指定 ASIN（逗号分隔）")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不更新数据库")
    args = parser.parse_args()

    import asyncio

    anchor = _parse_date(args.date) if args.date else date.today()
    if anchor is None:
        print(f"无效日期: {args.date}")
        return
    asins = None
    if args.asins:
        asins = {a.strip().upper() for a in args.asins.split(",") if a.strip()}

    stats = asyncio.run(recompute(anchor, asins, args.dry_run))
    print(f"\n结果: {stats}")


if __name__ == "__main__":
    main_cli()
