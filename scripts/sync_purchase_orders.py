# -*- coding: utf-8 -*-
"""领星采购订单待到货量同步（/api/purchase/orderListsV2）

功能：
  - 分页拉取采购订单（每页 50）；
  - 仅统计「待到货」状态的订单；
  - 每条 item 的 quantity_receive = 待到货量；
  - 匹配规则（新）：清洗采购单品名（见 clean_po_product_name），
    ASIN 品名包含清洗后采购单品名即配对成功；
    配件关键词数据（OPP袋/真空袋/说明书/背卡）直接剔除，不参与匹配；
    SKU 精确匹配兜底。
  - 汇总写入当日 inventory_snapshots.purchase_on_order（采购单待到货量）。
  - 同时写回 products.purchase_on_order（基础数据，分析直接用最新值）。

用法：
  python scripts/sync_purchase_orders.py            # 写库
  python scripts/sync_purchase_orders.py --dry-run  # 只统计
"""
import argparse
import os
import re
import sys
from collections import defaultdict
from datetime import date

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.services.lx_station import station_proxy
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://huizhixin.lingxing.com/api/purchase/orderListsV2"
PAGE_SIZE = 50
PENDING_STATUS = "待到货"


SUFFIX_TOKENS = ("亚拼", "亚版", "亚", "拼")


def _has_cjk(s: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in s)


def clean_product_name(name) -> str:
    """品名清洗，用于待到货按品名匹配 ASIN（优先于 SKU 匹配）

    规则：
      1) 首尾"-"分隔段去掉取中间：去掉开头纯英文/数字段（品牌/店铺代码），
         去掉结尾已知后缀段（如 亚拼/亚版）；
      2) 中间内容去掉 "XX版"（如 20版/24版/26版）。
    示例：Moon Boat-EA-2个冰吧+打气筒组合20版-亚拼 → 2个冰吧+打气筒组合
    """
    if not name:
        return ""
    raw = str(name).strip().replace("（", "(").replace("）", ")")
    parts = [p.strip() for p in raw.split("-") if p.strip()]
    if not parts:
        return ""
    # 去掉开头纯英文/数字段（品牌、店铺代码等）
    while len(parts) > 1 and parts[0] and not _has_cjk(parts[0]):
        parts.pop(0)
    # 去掉结尾已知后缀段（如 亚拼）
    while len(parts) > 1 and parts[-1] in SUFFIX_TOKENS:
        parts.pop()
    cleaned = "-".join(parts)
    # 去掉中间 "XX版"
    cleaned = re.sub(r"\d+版", "", cleaned)
    return cleaned.strip(" -")


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（用于子件品名归属主产品）"""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                if dp[i][j] > best:
                    best = dp[i][j]
    return best


def match_sub_item_asin(sub_cleaned: str, name_candidates: list, name_lookup: dict) -> str | None:
    """子件（子-）品名 → 主产品 ASIN

    规则：优先「包含关系」（子件品名含产品品名 或 产品品名含子件品名，候选按长度降序取最具体）；
    否则取最长公共子串 ≥4 字 的最优命中。
    """
    if not sub_cleaned:
        return None
    best_asin, best_score = None, 0
    for cand in name_candidates:
        if not cand:
            continue
        if cand in sub_cleaned or sub_cleaned in cand:
            return name_lookup[cand]
        score = _lcs_len(sub_cleaned, cand)
        if score >= 4 and score > best_score:
            best_score = score
            best_asin = name_lookup[cand]
    return best_asin


# 配件关键词：出现在采购单品名中的配件数据，不参与 ASIN 匹配
ACCESSORY_KEYWORDS = ("opp袋", "真空袋", "说明书", "背卡")


def _is_accessory(name: str) -> bool:
    """命中配件关键词 → True（该明细应剔除）"""
    if not name:
        return False
    lowered = name.lower()
    return any(kw in lowered for kw in ACCESSORY_KEYWORDS)


def clean_po_product_name(name) -> str:
    """采购单品名清洗（新规则，仅采购订单通道使用）

    命名规则：前缀（子-）+品名+配件后缀。
      - 含"子-"（前缀） → 去掉"子-"，再去掉第一个"-"之后所有内容，仅保留品名；
      - 不含"子-"       → 保持原样。
    示例：
      子-2个冰吧+打气筒组合20版-亚拼 → 2个冰吧+打气筒组合20版
      2个冰吧+打气筒组合20版-亚拼   → 2个冰吧+打气筒组合20版-亚拼
    """
    if not name:
        return ""
    raw = str(name).strip()
    if "子-" in raw:
        rest = raw.split("子-", 1)[1].strip()
        return rest.split("-", 1)[0].strip() if rest else ""
    return raw


def _pick_best_name_match(cleaned: str, product_rows: list) -> str | None:
    """ASIN 品名包含清洗后采购单品名 → 取最具体（品名最短）的 ASIN；无命中返回 None"""
    if not cleaned:
        return None
    best_asin, best_len = None, None
    for asin, pname in product_rows:
        if cleaned in pname and (best_len is None or len(pname) < best_len):
            best_len = len(pname)
            best_asin = asin
    return best_asin


def _payload(offset: int, seq: int) -> dict:
    return {
        "offset": offset,
        "length": PAGE_SIZE,
        "expect_arrive_time_status": "",
        "sort_field": "create_time",
        "sort_type": "desc",
        "status_shipped": [],
        "status": 2,  # 2=待到货，只拉待到货订单
        "pay_status": [],
        "search_field_time": "create_time",
        "search_field": "order_sn",
        "search_value": "",
        "wid": [],
        "sid": [],
        "gtag_ids": "",
        "permission_uid_list": [],
        "senior_search_list": [],
        "is_urgent": "",
        "change_order_status": "",
        "is_bad": "",
        "is_tax": "",
        "is_logistics": "",
        "is_associate_return": 0,
        "is_associate_exchange": 0,
        "supplier_ids": [],
        "bids": [],
        "cids": [],
        "is_transparency": "",
        "record_tag_ids": [],
        "record_tag_condition": "equal",
        "search_options": [],
        "max_item_number": 10,
        "req_time_sequence": f"/api/purchase/orderListsV2$${seq}",
    }


def fetch_all_orders() -> list:
    """拉取待到货采购订单（接口 status=2 过滤）

    经领星 API 服务站转发（登录态由服务端注入），auto_pagination 一次拉全量；
    服务端返回的 data 即订单扁平行列表（按 order_sn 去重）。
    """
    body = _payload(0, 1)
    resp = station_proxy(url=API_URL, body=body, auto_pagination=True)
    collect_raw("orderListsV2", resp, url=API_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[采购订单orderListsV2]调用失败: {resp.get('message')}")
    orders = []
    seen_sn = set()
    for o in (resp.get("data") or []):
        if not isinstance(o, dict):
            continue
        sn = o.get("order_sn")
        if sn and sn in seen_sn:
            continue
        if sn:
            seen_sn.add(sn)
        orders.append(o)
    return orders


async def main(dry_run: bool = False) -> dict:
    orders = fetch_all_orders()
    pending = [o for o in orders if (o.get("status_text") or "") == PENDING_STATUS]
    print(f"采购订单: {len(orders)} 条，待到货: {len(pending)} 条")

    # SKU 精确匹配映射 + ASIN 品名（用于「包含」模糊匹配，品名优先）
    async with async_session_factory() as s:
        rows = (await s.execute(
            select(Product.asin, Product.product_name, Product.local_sku, Product.msku, Product.fnsku)
        )).all()
        sku_lookup = {}
        product_rows = []
        for asin, product_name, local_sku, msku, fnsku in rows:
            for v in (local_sku, msku, fnsku):
                if v:
                    sku_lookup[str(v).strip().lower()] = asin
            if product_name:
                product_rows.append((asin, product_name))

    pending_by_asin = defaultdict(int)
    unmatched = []
    item_count = 0
    accessory_count = 0
    for o in pending:
        for it in o.get("item_list") or []:
            sku = (it.get("sku") or "").strip()
            item_name = (it.get("product_name") or "").strip()
            qty = int(float(it.get("quantity_receive") or 0))
            if qty <= 0 or (not sku and not item_name):
                continue
            # 配件（OPP袋/真空袋/说明书/背卡）直接剔除，不参与匹配
            if _is_accessory(item_name):
                accessory_count += 1
                continue
            item_count += 1
            # 品名包含匹配优先（ASIN 品名包含清洗后采购单品名），其次 SKU 精确匹配
            cleaned = clean_po_product_name(item_name) if item_name else ""
            asin = _pick_best_name_match(cleaned, product_rows) if cleaned else None
            if not asin and sku:
                asin = sku_lookup.get(sku.lower())
            if asin:
                pending_by_asin[asin] += qty
            else:
                unmatched.append((sku, qty, item_name))

    print(f"待到货明细 {item_count} 条（剔除配件 {accessory_count}），"
          f"匹配 ASIN {len(pending_by_asin)} 个，未匹配 {len(unmatched)} 条")
    for sku, qty, name in unmatched[:10]:
        print(f"  未匹配: {sku} x{qty} ({name[:40]})")
    for asin, qty in sorted(pending_by_asin.items(), key=lambda x: -x[1])[:10]:
        print(f"  {asin}: 待到货 {qty}")

    if dry_run:
        return {"orders": len(orders), "pending": len(pending), "asins": len(pending_by_asin)}

    written = 0
    if not dry_run:
        today = date.today()
        # Phase 1 / G-17：products 改批量写入（分块短事务 + 按 asin 排序 + lock_timeout + 死锁重试）
        from app.services.db_bulk import update_products_purchase_on_order

        written = await update_products_purchase_on_order(async_session_factory, pending_by_asin)
        async with async_session_factory() as s:
            # 当日快照同步（与 products 分开事务，缩短锁持有时间）
            for asin, qty in sorted(pending_by_asin.items()):
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
    print(f"已写入 inventory_snapshots.purchase_on_order：{written} 个 ASIN（{date.today()}）")
    await flush_raw()
    return {"orders": len(orders), "pending": len(pending), "asins": written}


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run))
