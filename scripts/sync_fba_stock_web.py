# -*- coding: utf-8 -*-
"""FBA 库存快照同步（领星网页 API showOnline 兜底通道）

用途：领星 MCP（query_fba_valid_list / get_fba_stock_list）Key 失效或限流时，
用网页 API showOnline（LX_AUTH_TOKEN，每日销量同步同源通道）全量抓取库存字段兜底，
保证 products / inventory_snapshots 始终有最新库存数据。

字段映射（msku 级 → ASIN）：
  afn_fulfillable_quantity → fba_available（FBA可售）
  afn_reserved_quantity    → fba_reserved（FBA预留）
  afn_inbound_shipped_quantity → fba_inbound（FBA在途）
同 ASIN 多 msku 时取 FBA可售最大的一行（与 listing 导入去重口径一致，避免重复计数）。

用法:
  python scripts/sync_fba_stock_web.py            # 写库
  python scripts/sync_fba_stock_web.py --dry-run  # 只统计
"""
import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from scripts.daily_sales_snapshot import fetch_all
from app.services.raw_store import flush_raw


def _to_int(v):
    try:
        return int(float(v)) if v is not None else 0
    except (ValueError, TypeError):
        return 0


def fetch_all_stock_web() -> list:
    """showOnline 全量抓取（经领星 API 服务站一次拉全量）"""
    items, _total = fetch_all(1)
    return items


def extract_stock(items: list) -> dict:
    """msku 级 → ASIN 级库存：FBA可售大为基础 + 列级非0合并

    同一 ASIN 多 msku 时，以 FBA可售最大的一行为基础，
    基础行为 0 的数值列取其他在售行的非 0 值合并（如 elf hat 可售75/在途0
    + jingle bell hat 可售0/在途521 → 合并可售75/在途521，不丢在途数据）。
    """
    from utils.asin_merge import merge_asin_records

    agg: dict = {}
    for it in items:
        asin = (it.get("asin") or "").strip()
        if not asin:
            continue
        stock = {
            "asin": asin,
            "msku": it.get("msku"),
            "fba_available": _to_int(it.get("afn_fulfillable_quantity")),
            "fba_reserved": _to_int(it.get("afn_reserved_quantity")),
            "fba_inbound": _to_int(it.get("afn_inbound_shipped_quantity")),
        }
        agg[asin] = merge_asin_records(agg.get(asin), stock, vol_key="fba_available") or stock
    return agg


async def sync_fba_stock_web(dry_run: bool = False) -> int:
    """网页 API 兜底同步；返回写入条数（0 表示无可写数据/失败）"""
    print("开始拉取领星 showOnline 全量库存（网页 API 兜底通道）...")
    items = fetch_all_stock_web()
    stocks = extract_stock(items)
    print(f"showOnline 返回 {len(items)} 条, 去重 {len(stocks)} 个 ASIN")
    if not stocks:
        print("[WARN] showOnline 未返回库存数据")
        return 0

    # 抽检样例
    for a in ("B0CXHTDX97", "B01FS7W9MQ"):
        if a in stocks:
            st = stocks[a]
            print(f"  样例 {a}: 可售={st['fba_available']} 预留={st['fba_reserved']} 在途={st['fba_inbound']}")

    if dry_run:
        return len(stocks)

    today = date.today()
    # 只同步 products 中已存在的 ASIN（避免删除的/外部的 ASIN 触发外键或重新引入，
    # 如 B0DHTJZ2YC 等已删除产品仍出现在 showOnline）
    async with async_session_factory() as s:
        exist = (await s.execute(
            select(Product.asin).where(Product.asin.in_(list(stocks.keys())))
        )).scalars().all()
    exist_set = set(exist)
    stocks = {a: st for a, st in stocks.items() if a in exist_set}
    print(f"过滤后写入 {len(stocks)} 个 ASIN（products 中存在）")
    if not stocks:
        return 0

    # Phase 1 / G-17：改为 db_bulk 分块短事务（按 asin 升序 + lock_timeout + 死锁重试），
    # 不再用"单事务逐行 UPDATE 4767 行"，也不再分两步做"清快照 → 写快照"。
    from app.services.db_bulk import replace_inventory_snapshot, update_products_fba_stock

    written = await update_products_fba_stock(
        async_session_factory,
        {a: (st["fba_available"], st["fba_reserved"], st["fba_inbound"])
         for a, st in stocks.items()},
    )
    snap = await replace_inventory_snapshot(
        async_session_factory, today,
        {a: {"fba_available": st["fba_available"],
             "fba_reserved": st["fba_reserved"],
             "fba_inbound": st["fba_inbound"]}
         for a, st in stocks.items()},
    )
    print(f"已写入 products FBA 库存 {written} 行、inventory_snapshots {snap} 条（{today}，网页API兜底）")
    await flush_raw()
    return written


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(sync_fba_stock_web(args.dry_run))


if __name__ == "__main__":
    main_cli()
