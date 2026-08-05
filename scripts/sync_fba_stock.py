"""FBA 库存快照同步脚本（领星 query_fba_valid_list 补货建议全量）

数据源：领星 MCP query_fba_valid_list（分页拉取全量 3991 条）
包含：海外仓库存(overseaStock=本地+在途)、总库存、FBA可售天数、断货日期、建议补货量
按 displayInfo.asinList 匹配 ASIN → 写入 inventory_snapshots 表（每日快照，幂等）

字段映射：
  可用库存(可售+预留+在途)  → available_stock  (overseaStock)
  海外仓库存                → local_stock     (overseaStock - fba部分近似)
  FBA可售天数               → fba_available_days (availableSaleDaysFba)
  断货预警日期              → stockout_date
  预计日均销量              → estimated_daily_sales
  建议补货量                → suggest_qty

用法: python scripts/sync_fba_stock.py [--dry-run]
"""
import argparse
import asyncio
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete
from app.database import async_session_factory
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.services.mcp_client import mcp_call

PAGE_SIZE = 200


def fetch_all_stock() -> list:
    """分页拉取领星补货建议全量数据"""
    items = []
    offset = 0
    while True:
        r = mcp_call("lingxing", "query_fba_valid_list", {
            "length": PAGE_SIZE,
            "offset": offset,
        })
        data = r.get("data", {})
        inner = data.get("data", data) if isinstance(data, dict) else data
        lst = inner.get("list", []) if isinstance(inner, dict) else []
        items.extend(lst)
        if len(lst) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return items


def extract_stock(item: dict) -> list:
    """从单条补货建议提取库存字段（asinList 中每个 ASIN 都映射同一份库存）"""
    d = item.get("displayInfo", {})
    asins = d.get("asinList", []) or []
    if not asins:
        return []
    s = item.get("suggestInfo", {}) or {}
    st = item.get("stockQuantityInfo", {}) or {}
    results = []
    for asin in asins:
        results.append({
            "asin": asin,
            "available_stock": int(float(st.get("overseaStock") or 0)),
            "stock_total": int(float(st.get("stockTotal") or 0)),
            "fba_available_days": _safe_int(s.get("availableSaleDaysFba")),
            "stockout_date": s.get("outStockDate"),
            "estimated_daily_sales": _safe_float(s.get("estimatedSaleAvgQuantity")),
            "suggest_qty": _safe_int(s.get("quantitySugPurchase")),
            "out_stock_flag": _safe_int(s.get("outStockFlag")),
        })
    return results


def _safe_int(v, default=0):
    try:
        return int(float(v)) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


async def sync_fba_stock(dry_run: bool = False) -> int:
    """同步 FBA 库存快照（领星 MCP，每日幂等）；返回写入的库存条数"""
    print("开始拉取领星补货建议全量数据...")
    items = fetch_all_stock()
    print(f"领星返回 {len(items)} 条")

    stocks = {}
    for item in items:
        for ext in extract_stock(item):
            if not ext["asin"]:
                continue
            # 同一 ASIN 可能挂在多个组合下：保留可用库存最大的记录
            prev = stocks.get(ext["asin"])
            if prev is None or ext["available_stock"] > prev["available_stock"]:
                stocks[ext["asin"]] = ext
    print(f"解析出 {len(stocks)} 个 ASIN 库存")

    s = async_session_factory()
    try:
        async with s:
            if not dry_run:
                # 补齐缺失产品档案
                asins = list(stocks.keys())
                exist = (await s.execute(select(Product.asin).where(Product.asin.in_(asins)))).scalars().all()
                missing = [a for a in asins if a not in set(exist)]
                if missing:
                    for a in missing:
                        s.add(Product(asin=a, product_name=f"待补全 {a}", status=True))
                    print(f"补齐缺失产品档案 {len(missing)} 条")

                today = date.today()
                # 幂等：清当日旧快照
                await s.execute(delete(InventorySnapshot).where(InventorySnapshot.snapshot_date == today))
                for asin, st in stocks.items():
                    s.add(InventorySnapshot(
                        asin=asin,
                        snapshot_date=today,
                        fba_available=st["available_stock"],
                        fba_reserved=0,
                        fba_inbound=st["stock_total"] - st["available_stock"],
                        local_stock=st["available_stock"],
                        purchase_on_order=st["suggest_qty"],
                    ))
                await s.commit()
                print(f"已写入 inventory_snapshots {len(stocks)} 条（{today}）")

            # 示例
            for asin in ["B0CXHTDX97", "B01FS7W9MQ"]:
                if asin in stocks:
                    st = stocks[asin]
                    print(f"\n示例 {asin}: 可用={st['available_stock']} 总={st['stock_total']} FBA可售天数={st['fba_available_days']} 断货日={st['stockout_date']} 日均={st['estimated_daily_sales']} 建议补货={st['suggest_qty']}")
            return len(stocks)
    finally:
        await s.close()


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(sync_fba_stock(args.dry_run))


if __name__ == "__main__":
    main_cli()
