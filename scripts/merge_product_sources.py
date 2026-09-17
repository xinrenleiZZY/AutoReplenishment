"""多方数据源合并：产品管理(product/lists) + 产品列表(showOnline) 合并最全字段

品名优先级：产品管理 product_name > listing local_name > item_name
数据源：
  - p_id/msku_id_clean_full.json   (listing: asin/msku/local_name/item_name/品牌/店铺/站点/售价/FNSKU)
  - p_id/box_qty_raw.jsonl         (产品管理: sku/product_name/category_name/status_text/cg_box_pcs/负责人)

用法: python -m scripts.merge_product_sources [--dry-run]
"""

import argparse
import asyncio
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models.product import Product

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LISTING_JSON = os.path.join(BASE, "p_id", "msku_id_clean_full.json")
ERP_JSONL = os.path.join(BASE, "p_id", "box_qty_raw.jsonl")

logger = logging.getLogger(__name__)


def parse_erp_category(category_name: str) -> str | None:
    """解析 ERP 分类名：取最后一段并去掉前缀编号，如 'YPS\\K8-塑料类' -> '塑料类'"""
    if not category_name:
        return None
    seg = category_name.strip().split("\\")[-1].strip()
    if not seg:
        return None
    # 去掉 'K8-' / 'F15-' 之类编号前缀
    import re
    seg = re.sub(r"^[A-Za-z]+\d+[-_]", "", seg)
    return seg[:100] or None


def load_listing() -> tuple[dict, dict, dict, dict]:
    """返回 (asin->字段, msku->asin, local_sku->asin, local_name->asin)"""
    if not os.path.exists(LISTING_JSON):
        logger.warning(f"缺少产品数据文件: {LISTING_JSON}，跳过 listing 合并")
        return {}, {}, {}, {}
    with open(LISTING_JSON, "r", encoding="utf-8") as f:
        items = json.load(f)
    by_asin = {}
    msku_map = {}
    local_sku_map = {}
    local_name_map = {}
    for item in items:
        asin = (item.get("asin") or "").strip()
        msku = (item.get("msku") or "").strip()
        local_sku = (item.get("local_sku") or "").strip()
        local_name = (item.get("local_name") or "").strip()
        if asin:
            cur = by_asin.get(asin)
            # 重复 ASIN 优先保留含 local_name 的记录
            if cur is None or (local_name and not (cur.get("local_name") or "").strip()):
                by_asin[asin] = item
        if msku and asin:
            msku_map[msku] = asin
        if local_sku and asin:
            local_sku_map.setdefault(local_sku, asin)
        if local_name and asin:
            local_name_map.setdefault(local_name, asin)
    return by_asin, msku_map, local_sku_map, local_name_map


def load_erp() -> dict:
    """返回 sku -> 产品管理字段"""
    if not os.path.exists(ERP_JSONL):
        logger.warning(f"缺少箱规数据文件: {ERP_JSONL}，跳过 ERP 合并")
        return {}
    result = {}
    with open(ERP_JSONL, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            sku = (item.get("sku") or "").strip()
            if sku:
                result[sku] = item
    return result


def merge_values(listing: dict, erp: dict | None) -> dict:
    """按优先级合并字段"""
    values = {}
    erp_name = ((erp or {}).get("product_name") or "").strip()
    local_name = (listing.get("local_name") or "").strip()
    item_name = (listing.get("item_name") or "").strip()
    if erp_name and not erp_name.startswith("子-"):
        product_name = erp_name
    elif local_name:
        product_name = local_name
    elif erp_name:
        product_name = erp_name
    else:
        product_name = item_name
    product_name = product_name[:2000]
    if not product_name.strip():
        product_name = f"待补全 {listing.get('asin', '')}"
    if product_name:
        values["product_name"] = product_name
    if item_name:
        values["listing_title"] = item_name
    if listing.get("msku"):
        values["msku"] = listing["msku"]
    if erp:
        if erp.get("category_name"):
            cat = parse_erp_category(erp.get("category_name"))
            if cat:
                values["category"] = cat
        box = erp.get("cg_box_pcs")
        if box and int(box) > 0:
            values["box_quantity"] = int(box)
        if erp.get("product_creator_realname"):
            values["operator"] = erp["product_creator_realname"]
        if erp.get("brand_name"):
            values["brand"] = erp["brand_name"]
        if erp.get("supplier_name"):
            values["supplier_name"] = erp["supplier_name"]
        if erp.get("cg_price") not in (None, ""):
            values["cost_price"] = str(erp["cg_price"])
        if erp.get("product_developer"):
            values["product_developer"] = erp["product_developer"]
        if erp.get("product_creator_realname"):
            values["product_creator_realname"] = erp["product_creator_realname"]
    brand = (listing.get("seller_brand") or "").strip()
    if brand:
        values["brand"] = brand
    for k in ("shop", "marketplace", "fnsku", "category_id"):
        if listing.get(k) not in (None, ""):
            values[k] = listing[k]
    if listing.get("listing_price") is not None:
        values["price"] = str(listing["listing_price"])
    elif listing.get("price") is not None:
        values["price"] = str(listing["price"])
    return values


async def run_merge(dry_run: bool = False) -> dict:
    by_asin, msku_map, local_sku_map, local_name_map = load_listing()
    erp = load_erp()

    # sku -> (asin, 优先级): 1=msku/sku 2=local_sku/sku 3=品名精确匹配
    sku_asin: dict[str, tuple] = {}
    for sku, item in erp.items():
        asin = msku_map.get(sku)
        prio = 1
        if not asin:
            asin = local_sku_map.get(sku)
            prio = 2
        if not asin:
            pn = (item.get("product_name") or "").strip()
            if pn in local_name_map:
                asin = local_name_map[pn]
                prio = 3
        if asin:
            cur = sku_asin.get(sku)
            if cur is None or prio < cur[1]:
                sku_asin[sku] = (asin, prio)

    # 每个 ASIN 关联的所有 ERP 记录，选最优（主品优先）
    asin_erp: dict[str, list] = {}
    for sku, (asin, prio) in sku_asin.items():
        asin_erp.setdefault(asin, []).append((prio, erp[sku]))

    def best_erp(candidates: list) -> dict | None:
        if not candidates:
            return None
        def rank(c):
            prio, rec = c
            name = (rec.get("product_name") or "")
            return (
                0 if not name.startswith("子-") else 1,   # 主品优先
                0 if rec.get("is_matched_listing") == 1 else 1,
                prio,
            )
        return min(candidates, key=rank)[1]

    merged = {}
    erp_matched = 0
    for sku, (asin, _prio) in sku_asin.items():
        if asin in merged:
            continue
        listing = by_asin.get(asin, {})
        erp_rec = best_erp(asin_erp.get(asin, []))
        if erp_rec is not None:
            erp_matched += 1
        merged[asin] = merge_values(listing, erp_rec)
    for asin, listing in by_asin.items():
        if asin not in merged:
            merged[asin] = merge_values(listing, None)

    print(f"listing ASIN: {len(by_asin)}, ERP 记录: {len(erp)}, ERP 匹配到 ASIN: {erp_matched}")

    if dry_run:
        names = sum(1 for v in merged.values() if v.get("product_name"))
        print(f"[dry-run] 将更新 {len(merged)} 个产品（含品名 {names}）")
        return {"total": len(merged), "erp_matched": erp_matched}

    s = async_session_factory()
    updated = 0
    try:
        async with s:
            asins = list(merged.keys())
            exist = await s.execute(select(Product.asin).where(Product.asin.in_(asins)))
            exist_set = set(exist.scalars().all())
            for i, (asin, values) in enumerate(merged.items()):
                # 维护字段（等级/生命周期/类型）不覆盖
                values = {k: v for k, v in values.items() if k not in
                          ("product_level", "life_cycle", "product_type", "calc_frequency",
                           "lead_time", "min_order_qty", "list_date", "profit_rate")}
                if not values:
                    continue
                if asin in exist_set:
                    await s.execute(update(Product).where(Product.asin == asin).values(**values))
                else:
                    s.add(Product(asin=asin, product_name=values.get("product_name", "待补全 " + asin),
                                  status=values.get("status", True), **{k: v for k, v in values.items()
                                                                       if k not in ("product_name", "status")}))
                updated += 1
                if (i + 1) % 500 == 0:
                    await s.flush()
            await s.commit()
            print(f"合并写入完成: 更新/新增 {updated} 个产品")
    finally:
        await s.close()
    return {"total": len(merged), "erp_matched": erp_matched, "updated": updated}


def main_cli():
    parser = argparse.ArgumentParser(description="多方数据源合并产品字段")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(run_merge(dry_run=args.dry_run))


if __name__ == "__main__":
    main_cli()
