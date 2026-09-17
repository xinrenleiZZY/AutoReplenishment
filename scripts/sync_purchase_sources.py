# -*- coding: utf-8 -*-
"""领星 采购计划 + 采购单看板 抓取脚本（每日凌晨全量入库）

数据源（两个网页API）：
  1) 采购计划(listNew) https://huizhixin.lingxing.com/api/module/purchase/plan/listNew
  2) 采购单看板      https://huizhixin.lingxing.com/api/purchase_report/purchaseOrderBoard

功能：
  - 分别分页抓取两个接口的「完整数据」；
  - 采购计划(listNew)：响应为 {list:[分组], total:N}，逐条明细落库 purchase_plan_items。
  - 采购单看板：data 为 {list:[...], total:N}，逐条落库 purchase_order_board。
  - 幂等：每次全量抓取先删除表内旧数据再写入（每天完整数据入库，每天更新）。

说明：本脚本负责「两个数据源完整落库」，并基于落库数据计算「ASIN 待到货量」：
      步骤1 采购计划明细(待采购/部分采购)品名 → plan_sn；
      步骤2 plan_sn → 采购单看板 relation_plan → wait_quantity；
      计算结果替换写入 products/inventory_snapshots.purchase_on_order。
      看板时间窗口默认最近7天，可用 --board-start/--board-end 覆盖。

用法：
  python scripts/sync_purchase_sources.py              # 全量抓取+写库
  python scripts/sync_purchase_sources.py --dry-run    # 只抓取统计不写库
"""

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete
from sqlalchemy import select
from sqlalchemy import update

from app.database import async_session_factory
from app.models.inventory import InventorySnapshot
from app.models.purchase_plan_items import PurchasePlanItem
from app.models.purchase_order_board import PurchaseOrderBoard
from app.models.product import Product
from app.services.raw_store import collect_raw, flush_raw

PLAN_NEW_URL = "https://huizhixin.lingxing.com/api/module/purchase/plan/listNew"
BOARD_URL = "https://huizhixin.lingxing.com/api/purchase_report/purchaseOrderBoard"
PAGE_SIZE = 500          # 每页拉取条数
REQUEST_INTERVAL = 0.3   # 翻页间隔，避免限流
MAX_PAGES = 200          # 防死循环上限

# 采购单看板抓取时间窗口（time_type=1），默认最近7天；start_date/end_date 可显式覆盖
BOARD_LOOKBACK_DAYS = 7
# 待到货量口径：步骤1 采购计划明细状态（待采购 + 部分采购）
PLAN_WAIT_STATUSES = ("待采购", "部分采购")


def _get_headers() -> dict:
    token = os.getenv("LX_AUTH_TOKEN", "")
    return {
        "accept": "application/json, text/plain, */*",
        "ak-client-type": "web",
        "ak-origin": "https://huizhixin.lingxing.com",
        "auth-token": token,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://huizhixin.lingxing.com",
        "referer": "https://huizhixin.lingxing.com/erp/msupply/purchasePlan",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/147.0.0.0 Safari/537.36",
        "x-ak-company-id": "90136117059997696",
        "x-ak-env-key": "huizhixin",
        "x-ak-language": "zh",
        "x-ak-platform": "1",
        "x-ak-request-source": "erp",
        "x-ak-uid": "11054904",
        "x-ak-version": "3.9.0.3.0.089",
        "x-ak-zid": "1",
        "baggage": "sentry-environment=production,sentry-release=3.9.0.3",
    }


def _board_payload(offset: int, length: int, seq: int, start_date: str, end_date: str) -> dict:
    return {
        "receive_status": "", "time_type": "1", "start_date": start_date, "end_date": end_date,
        "sid": "", "cg_uid": "", "product_id": [], "offset": offset, "length": length,
        "search_field": "sku", "search_value": "", "senior_search_list": [],
        "sort_field": "", "sort_type": "", "range_select": "[]", "display_dimension": "detail",
        "req_time_sequence": f"/api/purchase_report/purchaseOrderBoard$${seq}",
    }


def _board_window(start_date: str | None, end_date: str | None) -> tuple[str, str]:
    """看板时间窗口：缺省默认最近 BOARD_LOOKBACK_DAYS 天"""
    end = (end_date or date.today().isoformat()).strip()
    if start_date:
        return start_date.strip(), end
    start = (date.fromisoformat(end) - timedelta(days=BOARD_LOOKBACK_DAYS - 1)).isoformat()
    return start, end


def _plan_new_payload(offset: int, length: int, seq: int) -> dict:
    return {
        "offset": offset, "length": length, "sort_field": "creator_time", "sort_type": "desc",
        "status": "", "country_code": [], "sids": [], "wids": [], "search_field_time": "creator_time",
        "search_field": "product_name", "search_value": "", "senior_search_list": "[]",
        "audit_uid": [], "gtag_ids": "", "is_related_process_plan": "", "is_relate_shipment_plan": "",
        "is_combo": "", "creator_uids": [], "purchaser_id": [], "cg_uids": [], "cids": [], "bids": [],
        "purchase_status": "", "is_urgent": "", "supplier_ids": [],
        "req_time_sequence": f"/api/module/purchase/plan/listNew$${seq}",
    }


def _post(url, payload, headers, source):
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    collect_raw(source, data, url=url, method="POST", params=payload, status_code=resp.status_code)
    if data.get("code") != 1:
        raise RuntimeError(f"API error: code={data.get('code')} msg={data.get('msg')}")
    return data.get("data") or {}


# ── 采购单看板 ──
def fetch_board(headers, start_date: str | None = None, end_date: str | None = None) -> tuple[list[dict], int]:
    """全量抓取采购单看板，返回 (list, total)"""
    rows = []
    total = 0
    offset = 0
    seq = 1
    s_date, e_date = _board_window(start_date, end_date)
    for _ in range(MAX_PAGES):
        data = _post(BOARD_URL, _board_payload(offset, PAGE_SIZE, seq, s_date, e_date), headers, "purchase_order_board")
        lst = data.get("list") or []
        total = int(data.get("total") or 0)
        for it in lst:
            if isinstance(it, dict):
                rows.append(it)
        if len(lst) < PAGE_SIZE or (total and offset + len(lst) >= total):
            break
        offset += len(lst)
        seq += 1
        time.sleep(REQUEST_INTERVAL)
    else:
        print("[WARN] 采购单看板超过分页上限，已截断")
    return rows, total


# ── 采购计划(listNew 接口2：items 直接含 product_name/status_text/plan_sn) ──
def fetch_plan_new(headers) -> list[dict]:
    """全量抓取采购计划 listNew，返回所有 items 平铺列表

    listNew 响应顶层为 {list:[分组], total:N}，total 为分组数；每组含 items 明细。
    按 offset/length 分页抓取分组，把各组 items 平铺返回（并附上外层分组信息）。
    """
    rows = []
    offset = 0
    seq = 1
    total = 0
    for _ in range(MAX_PAGES):
        payload = _plan_new_payload(offset, PAGE_SIZE, seq)
        resp = requests.post(PLAN_NEW_URL, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        j = resp.json()
        collect_raw("purchase_plan_new", j, url=PLAN_NEW_URL, method="POST", params=payload, status_code=resp.status_code)
        if j.get("code") != 1:
            raise RuntimeError(f"API error: code={j.get('code')} msg={j.get('msg')}")
        grp_list = j.get("list") or []
        total = int(j.get("total") or 0)
        for g in grp_list:
            gid = _to_int(g.get("id"))
            ppg_sn = g.get("ppg_sn")
            ppg_sn_id = g.get("ppg_sn_id")
            g_status = _to_int(g.get("group_status"))
            for it in (g.get("items") or []):
                if not isinstance(it, dict):
                    continue
                it = dict(it)
                it["_group_id"] = gid
                it["_ppg_sn"] = ppg_sn
                it["_ppg_sn_id"] = ppg_sn_id
                it["_group_status"] = g_status
                rows.append(it)
        if len(grp_list) < PAGE_SIZE or (total and offset + len(grp_list) >= total):
            break
        offset += len(grp_list)
        seq += 1
        time.sleep(REQUEST_INTERVAL)
    else:
        print("[WARN] 采购计划(listNew)超过分页上限，已截断")
    return rows


async def sync_purchase_plan_items(headers, stats, dry_run):
    """抓取采购计划 listNew 并落库 purchase_plan_items"""
    rows = fetch_plan_new(headers)
    stats["plan_items_total"] = len(rows)
    print(f"采购计划(listNew): {len(rows)} 条明细")

    if dry_run:
        return

    fetch_date = date.today()
    records = []
    for it in rows:
        records.append({
            "plan_sn": it.get("plan_sn"),
            "plan_id": it.get("plan_id"),
            "ppg_sn": it.get("_ppg_sn"),
            "ppg_sn_id": it.get("_ppg_sn_id"),
            "group_id": it.get("_group_id"),
            "product_id": _to_int(it.get("product_id")),
            "product_name": it.get("product_name"),
            "product_model": it.get("product_model"),
            "sku": it.get("sku"),
            "fnsku": it.get("fnsku"),
            "brand_name": it.get("brand_name"),
            "category_name": it.get("category_name"),
            "supplier_id": _to_int(it.get("supplier_id")),
            "supplier_name": it.get("supplier_name"),
            "purchaser_id": _to_int(it.get("purchaser_id")),
            "purchaser_name": it.get("purchaser_name"),
            "cg_uid": _to_int(it.get("cg_uid")),
            "sid": _to_int(it.get("sid")),
            "wid": _to_int(it.get("wid")),
            "wid_name": it.get("wid_name"),
            "warehouse_name": it.get("warehouse_name"),
            "quantity_plan": _to_int(it.get("quantity_plan")),
            "quantity_purchased": _to_int(it.get("quantity_purchased")),
            "cases_num": _to_int(it.get("cases_num")),
            "quantity_per_case": _to_int(it.get("quantity_per_case")),
            "pp_id": _to_int(it.get("pp_id")),
            "is_urgent": _to_int(it.get("is_urgent")),
            "is_combo": _to_int(it.get("is_combo")),
            "is_aux": _to_int(it.get("is_aux")),
            "has_relation_order": _to_int(it.get("has_relation_order")),
            "expect_arrive_time": it.get("expect_arrive_time"),
            "status": _to_int(it.get("status")),
            "status_text": it.get("status_text"),
            "group_status": it.get("_group_status"),
            "row_index": _to_int(it.get("row_index")),
            "raw_data": json.dumps(it, ensure_ascii=False, default=str),
            "fetch_date": fetch_date,
        })

    async with async_session_factory() as s:
        await s.execute(delete(PurchasePlanItem))
        for rec in records:
            s.add(PurchasePlanItem(**rec))
        await s.commit()
        stats["plan_items_written"] = len(records)
    print(f"已写入 purchase_plan_items {len(records)} 条（{fetch_date}）")


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


async def sync_purchase_board(headers, stats, dry_run,
                              start_date: str | None = None, end_date: str | None = None):
    """抓取采购单看板并落库"""
    s_date, e_date = _board_window(start_date, end_date)
    lst, total = fetch_board(headers, start_date=s_date, end_date=e_date)
    stats["board_window"] = f"{s_date}~{e_date}"
    stats["board_total"] = total
    stats["board_records"] = len(lst)
    print(f"采购单看板({s_date}~{e_date}): total={total}, 抓取 {len(lst)} 条")

    if dry_run:
        return

    fetch_date = date.today()
    records = []
    for it in lst:
        # 看板 model 字段仅在为真实ASIN(10位字母数字)时才落库为 asin；
        # 其余情况该字段被用于填备注性文字(如"方丁：不含税单价…")，不入 asin 列(原始备注保存在 raw_data)。
        asin = it.get("model") or None
        if asin and not re.fullmatch(r"[A-Za-z0-9]{10}", str(asin).strip()):
            asin = None
        records.append({
            "purchase_order_sn": it.get("purchase_order_sn"),
            "relation_plan": it.get("relation_plan"),
            "product_id": _to_int(it.get("product_id")),
            "asin": asin,
            "product_name": it.get("product_name"),
            "sku": it.get("sku"),
            "msku": it.get("msku"),
            "fnsku": it.get("fnsku"),
            "purchase_quantity": _to_int(it.get("purchase_quantity")),
            "receive_quantity": _to_int(it.get("receive_quantity")),
            "quantity_diff": _to_int(it.get("quantity_diff")),
            "wait_quantity": _to_int(it.get("wait_quantity")),
            "status": _to_int(it.get("status")),
            "expect_arrive_time": it.get("expect_arrive_time"),
            "create_time": it.get("create_time"),
            "finish_time": it.get("finish_time"),
            "country": it.get("country"),
            "raw_data": json.dumps(it, ensure_ascii=False, default=str),
            "fetch_date": fetch_date,
        })

    async with async_session_factory() as s:
        await s.execute(delete(PurchaseOrderBoard))
        for rec in records:
            s.add(PurchaseOrderBoard(**rec))
        await s.commit()
        stats["board_written"] = len(records)
    print(f"已写入 purchase_order_board {len(records)} 条（{fetch_date}）")


# ── ASIN 待到货量（精准口径） ──
async def _plan_wait_sn_by_name(session) -> dict[str, set[str]]:
    """步骤1：采购计划明细(待采购/部分采购) → 品名 → plan_sn 集合"""
    rows = (await session.execute(
        select(PurchasePlanItem.product_name, PurchasePlanItem.plan_sn)
        .where(PurchasePlanItem.status_text.in_(PLAN_WAIT_STATUSES))
        .where(PurchasePlanItem.plan_sn.isnot(None))
    )).all()
    m: dict[str, set[str]] = defaultdict(set)
    for pname, plan_sn in rows:
        if pname:
            m[pname.strip()].add(str(plan_sn).strip())
    return m


async def _board_wait_by_plan(session) -> dict[str, int]:
    """步骤2：采购单看板 → relation_plan → wait_quantity 汇总"""
    rows = (await session.execute(
        select(PurchaseOrderBoard.relation_plan, PurchaseOrderBoard.wait_quantity)
        .where(PurchaseOrderBoard.relation_plan.isnot(None))
    )).all()
    m: dict[str, int] = defaultdict(int)
    for rp, wq in rows:
        if rp:
            m[str(rp).strip()] += (wq or 0)
    return m


async def sync_asin_wait_quantity(dry_run: bool = False) -> dict:
    """ASIN 待到货量（精准口径），替换 purchase_on_order。

    步骤1：本系统ASIN品名 = 采购计划明细品名(product_name)，
           状态 待采购/部分采购(status_text)，取 plan_sn。
    步骤2：用 plan_sn 匹配 采购单看板 relation_plan，取 wait_quantity。
    """
    async with async_session_factory() as s:
        name_to_plans = await _plan_wait_sn_by_name(s)
        plan_to_wait = await _board_wait_by_plan(s)
        prods = (await s.execute(
            select(Product.asin, Product.product_name)
        )).all()

    stats = {
        "plan_names": len(name_to_plans),
        "board_plans": len(plan_to_wait),
    }
    wait_by_asin: dict[str, int] = {}
    matched_plan_sum = 0
    for asin, pname in prods:
        if not pname:
            continue
        plans = name_to_plans.get(pname.strip())
        if not plans:
            continue
        total = sum(plan_to_wait.get(psn, 0) for psn in plans)
        if total > 0:
            wait_by_asin[asin] = total
            matched_plan_sum += len(plans)

    stats["matched_asins"] = len(wait_by_asin)
    stats["wait_total"] = sum(wait_by_asin.values())
    stats["matched_plans"] = matched_plan_sum
    stats["sample"] = sorted(wait_by_asin.items(), key=lambda x: -x[1])[:10]
    print(f"ASIN待到货量: 采购计划品名 {len(name_to_plans)} 个、看板计划 {len(plan_to_wait)} 个；"
          f"匹配 ASIN {len(wait_by_asin)} 个，合计 {stats['wait_total']}")
    for asin, qty in stats["sample"]:
        print(f"  {asin}: 待到货 {qty}")

    if dry_run:
        return stats

    today = date.today()
    async with async_session_factory() as s:
        # 先清零（全量口径，替换历史上旧的待到货量，避免残留）
        await s.execute(update(Product).values(purchase_on_order=0))
        await s.execute(
            update(InventorySnapshot)
            .where(InventorySnapshot.snapshot_date == today)
            .values(purchase_on_order=0)
        )
        for asin, qty in wait_by_asin.items():
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
    print(f"已替换写入 products/inventory_snapshots.purchase_on_order（精准ASIN待到货量）："
          f"{len(wait_by_asin)} 个 ASIN（{today}）")
    return stats


async def main(dry_run: bool = False, board_start: str | None = None, board_end: str | None = None) -> dict:
    headers = _get_headers()
    stats = {"dry_run": dry_run}
    try:
        await sync_purchase_plan_items(headers, stats, dry_run)
    except Exception as e:
        stats["plan_items_error"] = str(e)
        print(f"[ERROR] 采购计划(listNew)同步失败: {e}")
    try:
        await sync_purchase_board(headers, stats, dry_run, start_date=board_start, end_date=board_end)
    except Exception as e:
        stats["board_error"] = str(e)
        print(f"[ERROR] 采购单看板同步失败: {e}")
    try:
        await sync_asin_wait_quantity(dry_run=dry_run)
    except Exception as e:
        stats["wait_error"] = str(e)
        print(f"[ERROR] ASIN待到货量计算失败: {e}")
    await flush_raw()
    print(f"\n结果: {json.dumps(stats, ensure_ascii=False)}")
    return stats


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="领星 采购计划+采购单看板 抓取")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    parser.add_argument("--board-start", default=None, help="看板抓取起始日期(yyyy-mm-dd)，缺省默认最近7天")
    parser.add_argument("--board-end", default=None, help="看板抓取结束日期(yyyy-mm-dd)，缺省今天")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, board_start=args.board_start, board_end=args.board_end))
