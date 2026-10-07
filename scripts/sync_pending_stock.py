# -*- coding: utf-8 -*-
"""待到货量同步（首选通道：领星库存明细 api/storage/lists 的 pending_num）

数据源：领星 库存明细 api/storage/lists（经「领星 API 服务站」转发，登录态由服务端注入）
字段：pending_num = 待到货量（每条库存明细一个 SKU/品名）

匹配：品名清洗后精确匹配优先 → SKU(local_sku/msku/fnsku) 精确匹配兜底。
写库：products.purchase_on_order（先清零再写入，避免旧订单残留）+
      inventory_snapshots.purchase_on_order（当日行）。

用法：
    python -m scripts.sync_pending_stock                 # 写库
    python -m scripts.sync_pending_stock --dry-run       # 只统计
"""

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import date

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

load_dotenv()

from sqlalchemy import select, update  # noqa: E402

from app.database import async_session_factory  # noqa: E402
from app.models.inventory import InventorySnapshot  # noqa: E402
from app.models.product import Product  # noqa: E402
from app.services.lx_station import station_proxy  # noqa: E402
from scripts.sync_purchase_orders import (  # noqa: E402
    clean_product_name,
    match_sub_item_asin,
)
from app.services.raw_store import collect_raw, flush_raw  # noqa: E402

API_URL = "https://huizhixin.lingxing.com/api/storage/lists"
PAGE_SIZE = 500  # auto_pagination 翻页步长（供服务站逐页拉取）
# 只统计以下仓库的待到货（同和/久辉/义乌），其他仓库忽略
ALLOWED_WAREHOUSES = ("同和", "久辉", "义乌")


def fetch_all_items() -> list:
    """经领星 API 服务站拉取全量库存明细（auto_pagination），data 即扁平行列表"""
    payload = {
        "wid_list": "", "mid_list": "", "sid_list": "", "inventoryOwnership": -1,
        "cid_list": "", "bid_list": "", "principal_list": "", "product_type_list": "",
        "product_attribute": "", "product_status": "", "search_field": "product_name",
        "search_value": "", "is_sku_merge_show": 0, "is_hide_zero_stock": 0,
        "offset": 0, "length": PAGE_SIZE, "sort_field": "", "sort_type": "",
        "gtag_ids": "", "senior_search_list": "[]", "permission_uid_list": "",
        "country_code_list": "", "has_statistic": False,
        "req_time_sequence": "/api/storage/lists$$1",
    }
    resp = station_proxy(url=API_URL, body=payload, auto_pagination=True)
    collect_raw("storage_lists", resp, url=API_URL, method="POST", params=payload)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[库存明细]调用失败: {resp.get('message')}")
    items = [it for it in (resp.get("data") or []) if isinstance(it, dict)]
    total = int(resp.get("total") or len(items))
    if total and len(items) < total:
        raise RuntimeError(f"库存明细拉取不完整（{len(items)}/{total}），中止写入，走兜底通道")
    return items


def _to_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


async def sync_pending_stock(dry_run: bool = False) -> dict:
    """库存明细首选通道：pending_num → products/inventory_snapshots 待到货量"""
    items = fetch_all_items()
    print(f"库存明细共 {len(items)} 条")

    # SKU / 品名 → ASIN 映射
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(Product.asin, Product.product_name, Product.local_sku, Product.msku, Product.fnsku)
        )).all()
    sku_lookup = {}
    name_lookup = {}
    for asin, pn, ls, ms, fs in rows:
        for v in (ls, ms, fs):
            if v:
                sku_lookup[str(v).strip().lower()] = asin
        cn = clean_product_name(pn)
        if cn and cn not in name_lookup:
            name_lookup[cn] = asin
    name_candidates = sorted(name_lookup.keys(), key=len, reverse=True)

    pending_by_asin = defaultdict(int)
    unmatched = []
    skipped_warehouse = 0
    for it in items:
        wh = (it.get("wh_name") or "").strip()
        if not any(k in wh for k in ALLOWED_WAREHOUSES):
            skipped_warehouse += 1
            continue
        sku = (it.get("sku") or "").strip()
        pn = (it.get("product_name") or "").strip()
        qty = _to_int(it.get("pending_num"))
        if qty <= 0 or (not sku and not pn):
            continue
        # 品名匹配优先，SKU 兜底；同 ASIN 多条记录（多仓库/多 SKU）合并求和
        asin = name_lookup.get(clean_product_name(pn)) if pn else None
        if not asin and sku:
            asin = sku_lookup.get(sku.lower())
        # 子件（子-）也归属主产品：去“子-”后按包含/公共子串匹配
        if not asin and pn and pn.startswith("子-"):
            sub_cleaned = clean_product_name(pn[len("子-"):].strip())
            asin = match_sub_item_asin(sub_cleaned, name_candidates, name_lookup)
        if asin:
            pending_by_asin[asin] += qty
        else:
            unmatched.append((sku, qty, pn))

    stats = {
        "ok": True, "channel": "storage/lists",
        "total_items": len(items), "matched_asins": len(pending_by_asin),
        "unmatched_items": len(unmatched), "pending_total": sum(pending_by_asin.values()),
        "skipped_warehouse": skipped_warehouse,
        "warehouses": ["同和", "久辉", "义乌"],
        "sample": sorted(pending_by_asin.items(), key=lambda x: -x[1])[:10],
    }
    print(f"库存明细 {len(items)} 条（同和/久辉/义乌），匹配 ASIN {len(pending_by_asin)} 个，"
          f"未匹配 {len(unmatched)} 条，其他仓库跳过 {skipped_warehouse} 条")
    for sku, qty, pn in unmatched[:8]:
        print(f"  未匹配: {sku} x{qty} ({pn[:40]})")
    for asin, qty in sorted(pending_by_asin.items(), key=lambda x: -x[1])[:10]:
        print(f"  {asin}: 待到货 {qty}")

    if dry_run:
        return stats

    today = date.today()
    async with async_session_factory() as s:
        # 先清零（库存明细为全量口径，避免旧订单残留）：products 全部 + 当日快照全部
        await s.execute(update(Product).values(purchase_on_order=0))
        await s.execute(
            update(InventorySnapshot)
            .where(InventorySnapshot.snapshot_date == today)
            .values(purchase_on_order=0)
        )
        for asin, qty in pending_by_asin.items():
            await s.execute(
                update(Product).where(Product.asin == asin).values(purchase_on_order=qty)
            )
            row = (await s.execute(
                select(InventorySnapshot).where(
                    InventorySnapshot.asin == asin,
                    InventorySnapshot.snapshot_date == today,
                )
            )).scalar_one_or_none()
            if row:
                row.purchase_on_order = qty
            else:
                s.add(InventorySnapshot(
                    asin=asin, snapshot_date=today, purchase_on_order=qty,
                    fba_available=0, fba_reserved=0, fba_inbound=0,
                    fba_inbound_shipped=0, local_stock=0,
                ))
        await s.commit()
    print(f"已写入 products/inventory_snapshots.purchase_on_order：{len(pending_by_asin)} 个 ASIN（{today}）")
    return stats


def main_cli():
    parser = argparse.ArgumentParser(description="待到货量同步（库存明细 pending_num 首选通道）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(_run(args.dry_run))


async def _run(dry_run: bool):
    stats = await sync_pending_stock(dry_run)
    await flush_raw()
    return stats


if __name__ == "__main__":
    main_cli()
