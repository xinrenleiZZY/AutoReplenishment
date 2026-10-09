# -*- coding: utf-8 -*-
"""领星采购单产品信息同步（/api/purchase/orderListsV2）— 主表 + 明细子表

数据源：https://huizhixin.lingxing.com/api/purchase/orderListsV2
接口返回 data.list 为采购单数组，每个采购单含 item_list 明细数组。本脚本双表落库：

  - 主表 purchase_order   ：外层 list 每张采购单一行（字段名沿用接口原字段）。
  - 明细表 purchase_order_item：item_list 每行一行，purchase_order_id 关联主表 id。
  - 不过滤状态（status 留空），全部采购单一并入库。

模式与落库策略：
  - full      : 全量（不加日期条件，接口默认口径）；整表清空后重写（全量快照）。
  - recent30  : 近 30 天（顶层 start_date/end_date，create_time 维度）。
  - recent60  : 近 60 天（顶层 start_date/end_date，create_time 维度）。
  recent* 采用「窗口内替换」：仅删除 create_time 落在窗口内的主表行及其子表明细，再插入，
  保留窗口外的历史基线（避免每日定时抓取把历史清空）。

用法：
  python scripts/sync_purchase_order_items.py                 # 全量写库（mode=full）
  python scripts/sync_purchase_order_items.py --mode recent60 # 近60天写库
  python scripts/sync_purchase_order_items.py --mode recent30 # 近30天写库
  python scripts/sync_purchase_order_items.py --dry-run       # 只抓取不写库
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete, select

from app.database import async_session_factory
from app.models.purchase_order import PurchaseOrder
from app.models.purchase_order_item import PurchaseOrderItem
from app.services.lx_station import station_proxy
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://huizhixin.lingxing.com/api/purchase/orderListsV2"
# 每页行数：接口硬限制 length<=200；服务站自动翻页上限 100 页，
# 取 200 可使 200*100=20000 条覆盖当前全量(~17840)，避免被截断在 5000 条。
PAGE_SIZE = 200

# ── 主表字段（按用户规格顺序；create_at/req_id 为业务外或顶层字段） ──
MAIN_FIELDS = [
    "id", "order_id", "order_sn", "custom_order_sn", "supplier_id", "supplier_name",
    "is_supplier_auth", "is_tax", "purchase_type", "purchase_type_text", "qc_type", "qc_type_text",
    "wid", "ware_house_name", "status", "status_text", "purchase_sign_status",
    "purchase_sign_status_text", "purchase_sign_time", "shipping_price", "shipping_currency",
    "amount_total", "standard_amount_total", "source", "process_mode", "process_mode_text",
    "pay_status", "pay_status_text", "create_uid", "create_realname", "create_time", "finish_time",
    "total_product_gross_weight", "order_time", "remark", "sub_status", "sub_status_text",
    "other_currency", "other_fee", "purchase_currency", "purchase_rate", "fee_part_type",
    "fee_part_type_text", "settlement_method", "settlement_method_text", "settlement_description",
    "payment_method", "payment_method_text", "purchaser_id", "purchaser_name", "alibaba_order_sn",
    "alibaba_account_name", "alibaba_order_id", "alibaba_order_url", "alibaba_related_type",
    "seller_message", "trade_method", "trade_method_text", "post_fee", "refund_payment",
    "total_success_amount", "is_amount_equal", "is_goods_value_equal", "has_alibaba_amount_diff",
    "opt_uid", "opt_realname", "total_price", "other_currency_icon", "shipping_currency_icon",
    "purchase_currency_icon", "quantity_total", "quantity_receive", "quantity_entry", "quantity_real",
    "quantity_return", "quantity_exchange", "op_types", "can_quick_storage", "is_changing", "is_urgent",
    "item_num", "is_overdue", "overdue_days", "inbound_qc_num", "return_status", "in_stock_amounts",
    "return_amounts", "discount_amount", "pay_amount", "receive_amounts", "quantity_noticed",
    "quantity_unnotice", "logistics_info", "logistics_list", "custom_fields", "audit_info",
    "req_id", "create_at",
]

MAIN_TINY = {
    "is_supplier_auth", "is_tax", "purchase_type", "qc_type", "purchase_sign_status", "process_mode",
    "fee_part_type", "is_amount_equal", "is_goods_value_equal", "has_alibaba_amount_diff",
    "can_quick_storage", "is_changing", "is_urgent", "is_overdue",
}
MAIN_INT = {
    "status", "source", "pay_status", "sub_status", "settlement_method", "alibaba_related_type",
    "quantity_total", "quantity_receive", "quantity_entry", "quantity_real", "quantity_return",
    "quantity_exchange", "item_num", "overdue_days", "inbound_qc_num", "return_status",
    "quantity_noticed", "quantity_unnotice",
}
MAIN_BIGINT = {"id", "supplier_id", "wid", "create_uid", "purchaser_id", "opt_uid"}
MAIN_NUM = {
    "shipping_price", "amount_total", "standard_amount_total", "total_product_gross_weight",
    "other_fee", "purchase_rate", "post_fee", "refund_payment", "total_success_amount", "total_price",
    "in_stock_amounts", "return_amounts", "discount_amount", "pay_amount", "receive_amounts",
}
MAIN_DT = {"purchase_sign_time", "finish_time", "order_time"}
MAIN_DATE = {"create_time"}
MAIN_JSON = {"op_types", "logistics_info", "logistics_list", "custom_fields"}
MAIN_TEXT = {"remark", "settlement_description", "seller_message", "audit_info"}

# ── 明细子表字段（按用户规格顺序；purchase_order_id 为关联主表新增） ──
ITEM_FIELDS = [
    "id", "purchase_order_id", "status", "plan_sn", "product_id", "product_name", "wid",
    "ware_house_name", "is_gift", "pic_url", "sku", "is_first_purchase", "first_purchase_text",
    "fnsku", "sid", "tax_rate", "tax_amount", "price", "cg_price", "standard_price",
    "price_without_tax", "amount", "amount_without_tax", "quantity_plan", "quantity_real",
    "quantity_entry", "quantity_return", "quantity_exchange", "expect_arrive_time", "remark",
    "item_purchase_remark", "cases_num", "quantity_per_case", "is_aux", "spu", "sku_identifier",
    "spu_name", "is_combo", "is_delete", "is_related_process_plan", "seller_name", "country_name",
    "quantity_qc", "quantity_qc_prepare", "quantity_qc_none", "product_good_num", "product_bad_num",
    "available_status", "available_status_text", "product_shelf_num", "quantity_receive",
    "quantity_diff", "finish_reason", "quantity_unnoticed", "quantity_noticed", "msku",
    "change_status", "return_status", "exchange_status", "supplier_product_url", "attribute",
    "global_tags", "custom_fields", "product_unit", "arrival_time_type", "spec_info",
    "product_gross_weight", "combo_product_list", "combo_product_text", "plan_creator_list",
    "plan_creator_text",
]

ITEM_BIGINT = {"id", "purchase_order_id", "product_id", "wid"}
ITEM_INT = {
    "status", "quantity_plan", "quantity_real", "quantity_entry", "quantity_return",
    "quantity_exchange", "cases_num", "quantity_per_case", "quantity_qc", "quantity_qc_prepare",
    "quantity_qc_none", "product_good_num", "product_bad_num", "available_status", "quantity_receive",
    "quantity_diff", "quantity_unnoticed", "quantity_noticed", "change_status", "return_status",
    "exchange_status", "arrival_time_type",
}
ITEM_TINY = {"is_gift", "is_first_purchase", "is_aux", "is_combo", "is_delete", "is_related_process_plan"}
ITEM_NUM = {
    "tax_rate", "tax_amount", "price", "cg_price", "standard_price", "price_without_tax",
    "amount", "amount_without_tax", "product_gross_weight",
}
ITEM_DT = {"expect_arrive_time"}
ITEM_JSON = {
    "msku", "supplier_product_url", "attribute", "global_tags", "custom_fields", "spec_info",
    "combo_product_list", "plan_creator_list",
}
ITEM_TEXT = {"product_name", "remark", "item_purchase_remark", "finish_reason", "combo_product_text"}


# ── 类型转换（空值统一 None） ──
def _s(v):
    if v is None:
        return None
    s = str(v)
    return s if s != "" else None


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _tiny(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return 1 if v else 0
    return _to_int(v)


def _dec(v):
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _dt(v):
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _date(v):
    d = _dt(v)
    return d.date() if d else None


def _json(v):
    if v is None or v == "":
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return None
    return None


def _conv(v, cat: str):
    if cat == "tiny":
        return _tiny(v)
    if cat in ("int", "bigint"):
        return _to_int(v)
    if cat == "num":
        return _dec(v)
    if cat == "dt":
        return _dt(v)
    if cat == "date":
        return _date(v)
    if cat == "json":
        return _json(v)
    return _s(v)


def _cat(field: str) -> str:
    if field in MAIN_TINY:
        return "tiny"
    if field in MAIN_INT:
        return "int"
    if field in MAIN_BIGINT:
        return "bigint"
    if field in MAIN_NUM:
        return "num"
    if field in MAIN_DT:
        return "dt"
    if field in MAIN_DATE:
        return "date"
    if field in MAIN_JSON:
        return "json"
    if field in MAIN_TEXT:
        return "text"
    return "str"


def _icat(field: str) -> str:
    if field in ITEM_BIGINT:
        return "bigint"
    if field in ITEM_INT:
        return "int"
    if field in ITEM_TINY:
        return "tiny"
    if field in ITEM_NUM:
        return "num"
    if field in ITEM_DT:
        return "dt"
    if field in ITEM_JSON:
        return "json"
    if field in ITEM_TEXT:
        return "text"
    return "str"


def _build_order(o: dict, req_id: str | None) -> dict:
    rec: dict = {}
    for f in MAIN_FIELDS:
        if f == "create_at":
            rec[f] = datetime.now()
        elif f == "req_id":
            rec[f] = req_id
        else:
            rec[f] = _conv(o.get(f), _cat(f))
    return rec


def _build_item(it: dict, order_pk: int | None) -> dict:
    rec: dict = {"purchase_order_id": order_pk, "id": _to_int(it.get("id"))}
    for f in ITEM_FIELDS:
        if f in ("id", "purchase_order_id"):
            continue
        rec[f] = _conv(it.get(f), _icat(f))
    return rec


def build_payload(offset: int, seq: int,
                  start_date: str | None = None, end_date: str | None = None) -> dict:
    """构造 orderListsV2 请求体；recent30 模式追加顶层 start_date/end_date"""
    body = {
        "offset": offset,
        "length": PAGE_SIZE,
        "expect_arrive_time_status": "",
        "sort_field": "create_time",
        "sort_type": "desc",
        "status_shipped": [],
        "status": "",  # 不过滤状态：全部入库（含已完成）
        "pay_status": [],
        "search_field_time": "create_time",
        "search_field": "plan_sn",
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
        "max_item_number": 5,
        "req_time_sequence": f"/api/purchase/orderListsV2$${seq}",
    }
    if start_date:
        body["start_date"] = start_date
    if end_date:
        body["end_date"] = end_date
    return body


def fetch_all_orders(start_date: str | None = None,
                     end_date: str | None = None) -> tuple[list[dict], str | None]:
    """全量抓取采购单（含 item_list 明细）

    经领星 API 服务站转发（登录态由服务端注入），auto_pagination 自动翻页；
    返回 (data 采购单数组, req_id 顶层请求追踪号)。
    """
    body = build_payload(0, 1, start_date, end_date)
    resp = station_proxy(url=API_URL, body=body, auto_pagination=True)
    collect_raw("orderListsV2", resp, url=API_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[采购单产品信息orderListsV2]调用失败: {resp.get('message')}")
    req_id = resp.get("require_id") or resp.get("req_id")
    data = [o for o in (resp.get("data") or []) if isinstance(o, dict)]
    return data, req_id


def _recent_window(days: int) -> tuple[str, str]:
    """近 N 天窗口：end=今天，start=今天-N天（字符串，供接口顶层 start_date/end_date）"""
    end = date.today()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


async def main(mode: str = "full", dry_run: bool = False) -> dict:
    """抓取并双表落库（purchase_order + purchase_order_item），返回 stats"""
    stats: dict = {"mode": mode, "orders": 0, "items": 0, "written": 0, "total": 0, "errors": []}

    start_date = end_date = None
    if mode == "recent30":
        start_date, end_date = _recent_window(30)
        stats["window"] = f"{start_date}~{end_date}"
    elif mode == "recent60":
        start_date, end_date = _recent_window(60)
        stats["window"] = f"{start_date}~{end_date}"

    try:
        # 可靠性：网络抓取放线程，避免阻塞事件循环
        orders, req_id = await asyncio.to_thread(fetch_all_orders, start_date, end_date)
    except Exception as e:  # noqa: BLE001
        stats["errors"].append(str(e))
        print(f"[ERROR] 采购单产品信息抓取失败: {e}")
        return stats

    # 主表：按 id 去重，一条采购单一一行
    order_rows: dict = {}
    item_rows: dict = {}
    for o in orders:
        pk = _to_int(o.get("id"))
        if pk is not None:
            order_rows[pk] = _build_order(o, req_id)
        for it in (o.get("item_list") or []):
            if not isinstance(it, dict):
                continue
            iid = _to_int(it.get("id"))
            if iid is None:
                continue
            item_rows[iid] = _build_item(it, pk)

    stats["orders"] = len(order_rows)
    stats["items"] = len(item_rows)
    stats["total"] = len(order_rows) + len(item_rows)
    print(f"采购单 {stats['orders']} 张，明细 {stats['items']} 条"
          + (f"（{stats['window']}）" if stats.get("window") else ""))

    if dry_run:
        print("[dry-run] 未写库")
        return stats

    # 幂等写入：
    #  - full    : 整表清空后重写（全量快照）
    #  - recent* : 窗口内替换——仅删除 create_time 落在窗口内的主表行及其子表明细，再插入，
    #              保留窗口外的历史基线
    async with async_session_factory() as s:
        if mode in ("recent30", "recent60"):
            start_d = date.fromisoformat(start_date)
            end_d = date.fromisoformat(end_date)
            win_ids = select(PurchaseOrder.id).where(
                PurchaseOrder.create_time.between(start_d, end_d)
            )
            await s.execute(
                delete(PurchaseOrderItem).where(
                    PurchaseOrderItem.purchase_order_id.in_(win_ids)
                )
            )
            await s.execute(
                delete(PurchaseOrder).where(
                    PurchaseOrder.create_time.between(start_d, end_d)
                )
            )
        else:
            await s.execute(delete(PurchaseOrderItem))
            await s.execute(delete(PurchaseOrder))
        s.add_all([PurchaseOrder(**r) for r in order_rows.values()])
        await s.flush()
        s.add_all([PurchaseOrderItem(**r) for r in item_rows.values()])
        await s.commit()

    stats["written"] = stats["total"]
    print(f"已写入 purchase_order {stats['orders']} 条、purchase_order_item {stats['items']} 条")
    await flush_raw()
    return stats


def main_cli():
    parser = argparse.ArgumentParser(description="领星采购单产品信息同步(orderListsV2)")
    parser.add_argument("--mode", default="full", choices=["full", "recent30", "recent60"],
                        help="full=全量（默认）；recent60=近60天；recent30=近30天")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    args = parser.parse_args()

    import asyncio

    stats = asyncio.run(main(args.mode, args.dry_run))
    print(json.dumps(stats, ensure_ascii=False))
    if stats.get("errors"):
        print(f"部分失败: {stats['errors']}")


if __name__ == "__main__":
    main_cli()
