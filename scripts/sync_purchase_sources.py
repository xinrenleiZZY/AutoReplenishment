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

说明：本脚本负责「两个数据源完整落库」，并基于落库数据按「品名」汇总计算「ASIN 待到货量」：
      步骤1 最近3个月采购计划明细(status_text ∈ 已完成/待采购/部分采购) →
            品名 → [(plan_sn, status_text, quantity_plan)]，一个品名对应 n 个计划编号；
      步骤2 逐个计划编号按状态分支取值：
            - 待采购/部分采购：待到货量 = 计划采购量(quantity_plan)，累加；
            - 已完成：plan_sn → 采购单明细子表 purchase_order_item.plan_sn →
                      quantity_receive
                      (同一 plan_sn 在明细有多行时按 purchase_order_item.price
                       取最贵那一行，不求和；全部取不到价格才回退求和)，存在即累加；
      步骤3 合计该品名的待到货量，写回对应产品的 purchase_on_order。
     匹配侧（系统产品）只按品名取最终数值，取不到即为 0（不再有降级兜底逻辑）。
      看板时间窗口默认最近3个月（月份-3），可用 --board-start/--board-end 覆盖；--board-all 取全历史（不限时间）。

用法：
  python scripts/sync_purchase_sources.py              # 全量抓取+写库
  python scripts/sync_purchase_sources.py --dry-run    # 只抓取统计不写库
  python scripts/sync_purchase_sources.py --board-all  # 采购单看板抓全历史（不限时间）
"""

import argparse
import calendar
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal

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
from app.models.purchase_order_item import PurchaseOrderItem
from app.models.product import Product
from app.services.lx_station import station_enabled, station_proxy
from app.services.raw_store import collect_raw, flush_raw

PLAN_NEW_URL = "https://huizhixin.lingxing.com/api/module/purchase/plan/listNew"
BOARD_URL = "https://huizhixin.lingxing.com/api/purchase_report/purchaseOrderBoard"
PAGE_SIZE = 500          # auto_pagination 翻页步长（供服务站逐页拉取）

# 采购单看板抓取时间窗口（time_type=1），默认最近3个月（月份-3）；start_date/end_date 可显式覆盖
BOARD_LOOKBACK_MONTHS = 3
# --board-all 时看板起始日期（早于任何业务数据，等效“不限时间”全历史）
BOARD_ALL_START = "2000-01-01"
# 待到货量口径：采购计划时间维度=最近3个月（按外层分组 create_time 过滤）
PLAN_LOOKBACK_MONTHS = 3
# 参与计算的采购计划状态（其他状态舍去）
PLAN_STATUSES = ("已完成", "待采购", "部分采购")
# 其中「直接取计划采购量」的状态；其余(已完成)走采购单看板取 wait_quantity
PLAN_DIRECT_STATUSES = ("待采购", "部分采购")


def _months_ago(n: int, ref: date | None = None) -> date:
    """返回 ref（缺省今天）往前 n 个月的日期（月份-3口径：按日对齐，跨月不足则取当月最后一天）"""
    ref = ref or date.today()
    y, m = ref.year, ref.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(ref.day, calendar.monthrange(y, m)[1]))


def _within_lookback(create_time: str | None, cutoff: date) -> bool:
    """create_time(如 '2026-09-28 15:13:23') 是否在 cutoff 之后（含当日）"""
    if not create_time:
        return False
    try:
        return date.fromisoformat(str(create_time).strip()[:10]) >= cutoff
    except ValueError:
        return False


def _board_payload(offset: int, length: int, seq: int, start_date: str, end_date: str) -> dict:
    return {
        "receive_status": "", "time_type": "1", "start_date": start_date, "end_date": end_date,
        "sid": "", "cg_uid": "", "product_id": [], "offset": offset, "length": length,
        "search_field": "sku", "search_value": "", "senior_search_list": [],
        "sort_field": "", "sort_type": "", "range_select": "[]", "display_dimension": "detail",
        "req_time_sequence": f"/api/purchase_report/purchaseOrderBoard$${seq}",
    }


def _board_window(start_date: str | None, end_date: str | None,
                  all_time: bool = False) -> tuple[str, str]:
    """看板时间窗口：all_time=True 取全历史（BOARD_ALL_START 起）；否则缺省最近 BOARD_LOOKBACK_MONTHS 个月（月份-3）"""
    end = (end_date or date.today().isoformat()).strip()
    if all_time:
        return BOARD_ALL_START, end
    if start_date:
        return start_date.strip(), end
    start = _months_ago(BOARD_LOOKBACK_MONTHS, date.fromisoformat(end)).isoformat()
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


# ── 采购单看板 ──
def fetch_board(start_date: str | None = None, end_date: str | None = None) -> tuple[list[dict], int]:
    """全量抓取采购单看板，返回 (扁平行列表, total)

    经领星 API 服务站转发（登录态由服务端注入），auto_pagination 一次拉全量；
    服务端返回的 data 即扁平行列表。
    """
    s_date, e_date = _board_window(start_date, end_date)
    body = _board_payload(0, PAGE_SIZE, 1, s_date, e_date)
    resp = station_proxy(url=BOARD_URL, body=body, auto_pagination=True)
    collect_raw("purchase_order_board", resp, url=BOARD_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[采购单看板]调用失败: {resp.get('message')}")
    rows = [it for it in (resp.get("data") or []) if isinstance(it, dict)]
    total = int(resp.get("total") or len(rows))
    return rows, total


# ── 采购计划(listNew 接口2：items 直接含 product_name/status_text/plan_sn) ──
def fetch_plan_new() -> list[dict]:
    """全量抓取采购计划 listNew，返回所有 items 平铺列表

    经领星 API 服务站转发（登录态由服务端注入），auto_pagination 一次拉全量；
    服务端返回的 data 即分组数组（每组含 items 明细），逐组平铺并附外层分组信息。
    """
    body = _plan_new_payload(0, PAGE_SIZE, 1)
    resp = station_proxy(url=PLAN_NEW_URL, body=body, auto_pagination=True)
    collect_raw("purchase_plan_new", resp, url=PLAN_NEW_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[采购计划listNew]调用失败: {resp.get('message')}")
    grp_list = resp.get("data") or []
    rows = []
    for g in grp_list:
        if not isinstance(g, dict):
            continue
        gid = _to_int(g.get("id"))
        ppg_sn = g.get("ppg_sn")
        ppg_sn_id = g.get("ppg_sn_id")
        g_status = _to_int(g.get("group_status"))
        g_create_time = g.get("create_time")
        for it in (g.get("items") or []):
            if not isinstance(it, dict):
                continue
            it = dict(it)
            it["_group_id"] = gid
            it["_ppg_sn"] = ppg_sn
            it["_ppg_sn_id"] = ppg_sn_id
            it["_group_status"] = g_status
            it["_create_time"] = g_create_time
            rows.append(it)
    return rows


async def sync_purchase_plan_items(stats, dry_run):
    """抓取采购计划 listNew 并落库 purchase_plan_items"""
    rows = fetch_plan_new()
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
            "create_time": it.get("_create_time"),
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


async def sync_purchase_board(stats, dry_run,
                              start_date: str | None = None, end_date: str | None = None,
                              all_time: bool = False):
    """抓取采购单看板并落库"""
    s_date, e_date = _board_window(start_date, end_date, all_time=all_time)
    lst, total = fetch_board(start_date=s_date, end_date=e_date)
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


# ── ASIN 待到货量（品名级汇总口径） ──
async def _plan_3m_by_name(session, cutoff: date) -> dict[str, list[tuple[str, str, int]]]:
    """步骤1：最近3个月采购计划明细(已完成/待采购/部分采购) → 品名 → [(plan_sn, status_text, quantity_plan)]

    1个品名可对应 n 个计划编号(plan_sn)，由步骤2按状态分支取值后合计。
    时间维度按外层分组 create_time 过滤（cutoff 之前舍去）。
    """
    rows = (await session.execute(
        select(
            PurchasePlanItem.product_name,
            PurchasePlanItem.plan_sn,
            PurchasePlanItem.status_text,
            PurchasePlanItem.quantity_plan,
            PurchasePlanItem.create_time,
        )
        .where(PurchasePlanItem.status_text.in_(PLAN_STATUSES))
        .where(PurchasePlanItem.plan_sn.isnot(None))
    )).all()
    m: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
    for pname, plan_sn, status_text, qty, create_time in rows:
        if not pname or not _within_lookback(create_time, cutoff):
            continue
        m[pname.strip()].append((str(plan_sn).strip(), status_text, int(qty or 0)))
    return m


async def _item_wait_by_plan(session) -> dict[str, int]:
    """步骤2：采购单明细子表 → plan_sn → quantity_receive

    明细子表为子件级明细：同一 plan_sn 下会同时出现主产品与子件(说明书/背卡/松紧绳等)
    多行，直接相加会成倍重复计数。
    规则：取该 plan_sn 下 price 最大的那一行的 quantity_receive；
          若该 plan_sn 下所有行 price 均为空，则回退为原始求和。
    """
    rows = (await session.execute(
        select(
            PurchaseOrderItem.plan_sn,
            PurchaseOrderItem.price,
            PurchaseOrderItem.quantity_receive,
        )
        .where(PurchaseOrderItem.plan_sn.isnot(None))
    )).all()

    grouped: dict[str, list[tuple[Decimal | None, int]]] = defaultdict(list)
    for plan_sn, price, qty in rows:
        if plan_sn:
            grouped[str(plan_sn).strip()].append((price, qty or 0))

    m: dict[str, int] = {}
    for key, items in grouped.items():
        if len(items) == 1:
            m[key] = items[0][1]
            continue
        best_idx = -1
        best_price: Decimal | None = None
        for i, (price, _) in enumerate(items):
            if price is None:
                continue
            if best_price is None or price > best_price:
                best_price = price
                best_idx = i
        if best_idx >= 0:
            m[key] = items[best_idx][1]
        else:
            m[key] = sum(q for _, q in items)
    return m


async def sync_asin_wait_quantity(dry_run: bool = False) -> dict:
    """ASIN 待到货量（品名级汇总口径），替换 purchase_on_order。

    步骤1：最近3个月采购计划明细(已完成/待采购/部分采购) → 品名 → [(plan_sn, status, qty)]。
    步骤2：逐个计划编号按状态分支取值并累加：
           - 待采购/部分采购：待到货量 = 计划采购量(quantity_plan)；
           - 已完成：plan_sn → 采购单明细子表 plan_sn → quantity_receive（存在即累加）。
    步骤3：合计该品名待到货量，写回对应产品的 purchase_on_order（取不到即 0）。
    """
    cutoff = _months_ago(PLAN_LOOKBACK_MONTHS)
    async with async_session_factory() as s:
        name_plans = await _plan_3m_by_name(s, cutoff)
        plan_to_wait = await _item_wait_by_plan(s)
        prods = (await s.execute(
            select(Product.asin, Product.product_name)
        )).all()

    # 步骤2+3：品名 → 待到货量（各计划编号按状态分支取值后合计）
    wait_by_name: dict[str, int] = {}
    direct_plans = 0      # 待采购/部分采购：直接取计划采购量
    item_plans_hit = 0    # 已完成：命中采购单明细子表
    for key, plans in name_plans.items():
        total = 0
        for plan_sn, status_text, qty in plans:
            if status_text in PLAN_DIRECT_STATUSES:
                total += qty
                direct_plans += 1
            else:
                w = plan_to_wait.get(plan_sn)
                if w:
                    total += w
                    item_plans_hit += 1
        wait_by_name[key] = total

    # 匹配侧：系统产品按品名直接取最终数值，取不到即 0
    wait_by_asin: dict[str, int] = {}
    for asin, pname in prods:
        if not pname:
            continue
        qty = wait_by_name.get(pname.strip(), 0)
        if qty > 0:
            wait_by_asin[asin] = qty

    stats = {
        "cutoff": cutoff.isoformat(),
        "plan_names": len(name_plans),
        "plan_links": sum(len(v) for v in name_plans.values()),
        "item_plans": len(plan_to_wait),
        "direct_plans": direct_plans,
        "item_hit_plans": item_plans_hit,
        "matched_asins": len(wait_by_asin),
        "wait_total": sum(wait_by_asin.values()),
        "sample": sorted(wait_by_asin.items(), key=lambda x: -x[1])[:10],
    }
    print(f"ASIN待到货量: 采购计划品名 {len(name_plans)} 个(计划编号 {stats['plan_links']} 个)、"
          f"采购单明细计划 {len(plan_to_wait)} 个；按状态取值：待采购/部分采购 {direct_plans} 个、"
          f"已完成命中明细 {item_plans_hit} 个；匹配 ASIN {len(wait_by_asin)} 个，"
          f"合计 {stats['wait_total']}（{cutoff} 起）")
    for asin, qty in stats["sample"]:
        print(f"  {asin}: 待到货 {qty}")

    if dry_run:
        return stats

    today = date.today()
    # Phase 1 / G-17：清零走独立短事务；products 明细走批量写入（分块 + 统一加锁顺序 + 死锁重试）
    from app.services.db_bulk import replace_purchase_on_order

    # 原子替换：清零 + 写入同一事务（避免中途失败把所有产品待到货量清成 0）
    await replace_purchase_on_order(async_session_factory, wait_by_asin)
    async with async_session_factory() as s:
        # 当日快照清零 + 回写（与 products 分开事务）
        await s.execute(
            update(InventorySnapshot)
            .where(InventorySnapshot.snapshot_date == today)
            .values(purchase_on_order=0)
        )
        for asin, qty in sorted(wait_by_asin.items()):
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


async def main(dry_run: bool = False, board_start: str | None = None, board_end: str | None = None,
               board_all: bool = False) -> dict:
    """三步抓取（计划明细 / 采购单看板 / ASIN待到货量）。

    Phase 0 / G-14：本函数仍逐步骤容错（一步失败不影响其余步骤），但必须把
    「每步成败、条数、错误」显式放进 stats，供 scheduler 判定 success/partial/failed。
    """
    stats = {"dry_run": dry_run}
    stats["source"] = "station" if station_enabled() else "direct"
    steps: dict[str, dict] = {}
    t0 = time.monotonic()
    try:
        await sync_purchase_plan_items(stats, dry_run)
        steps["plan_items"] = {"ok": True, "total": stats.get("plan_items_total"),
                               "written": stats.get("plan_items_written")}
    except Exception as e:
        stats["plan_items_error"] = str(e)
        steps["plan_items"] = {"ok": False, "error": str(e)[:500]}
        print(f"[ERROR] 采购计划(listNew)同步失败: {e}")
    try:
        await sync_purchase_board(stats, dry_run, start_date=board_start, end_date=board_end, all_time=board_all)
        steps["board"] = {"ok": True, "total": stats.get("board_total"),
                          "written": stats.get("board_written")}
    except Exception as e:
        stats["board_error"] = str(e)
        steps["board"] = {"ok": False, "error": str(e)[:500]}
        print(f"[ERROR] 采购单看板同步失败: {e}")
    try:
        wait_stats = await sync_asin_wait_quantity(dry_run=dry_run)
        steps["wait_quantity"] = {"ok": True, "matched_asins": (wait_stats or {}).get("matched_asins"),
                                  "wait_total": (wait_stats or {}).get("wait_total")}
    except Exception as e:
        stats["wait_error"] = str(e)
        steps["wait_quantity"] = {"ok": False, "error": str(e)[:500]}
        print(f"[ERROR] ASIN待到货量计算失败: {e}")

    stats["steps"] = steps
    stats["ok"] = not any(stats.get(k) for k in ("plan_items_error", "board_error", "wait_error"))
    stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
    await flush_raw()
    print(f"\n结果: {json.dumps(stats, ensure_ascii=False)}")
    return stats


if __name__ == "__main__":
    import asyncio

    parser = argparse.ArgumentParser(description="领星 采购计划+采购单看板 抓取")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    parser.add_argument("--board-start", default=None, help="看板抓取起始日期(yyyy-mm-dd)，缺省默认最近3个月")
    parser.add_argument("--board-end", default=None, help="看板抓取结束日期(yyyy-mm-dd)，缺省今天")
    parser.add_argument("--board-all", action="store_true", help="采购单看板抓取全历史（不限时间，起始 2000-01-01，优先于 --board-start）")
    args = parser.parse_args()
    asyncio.run(main(args.dry_run, board_start=args.board_start, board_end=args.board_end,
                     board_all=args.board_all))
