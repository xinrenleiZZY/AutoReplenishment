# -*- coding: utf-8 -*-
"""领星销售统计报表抓取脚本（sales-statistics/report/list 网页API）

数据源：领星网页 API https://gw.lingxingerp.com/sales-statistics/report/list
功能：按月/按年查询 ASIN 维度销售统计，全部字段落库到 sales_statistics_reports 表
  - 嵌套/数组字段（inventory/trend_data/global_tags 等）以 JSON 字符串存储
  - 整条原始响应 raw_data 兜底（保证不丢字段）
  - 幂等：同一 ASIN 同一统计区间只保留最新一次抓取

用法:
    python scripts/sync_sales_statistics.py                          # 默认去年全年
    python scripts/sync_sales_statistics.py --start 2026-01-01 --end 2026-12-31
    python scripts/sync_sales_statistics.py --query-type volume --group-type asin
    python scripts/sync_sales_statistics.py --dry-run                # 只抓取不写库
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete, select

from app.database import async_session_factory
from app.models.sales_statistics import SalesStatisticsReport
from app.services.lx_station import station_proxy
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://gw.lingxingerp.com/sales-statistics/report/list"
PAGE_SIZE = 500      # 接口支持最大每页500条
REQUEST_INTERVAL = 0.3


def _build_payload(page: int, start: str, end: str, query_type: str, group_type: str,
                   seq: int, filter_date_type: str) -> dict:
    """构造 report/list 请求体；page/pageSize 由服务站 auto_pagination 自动翻页"""
    return {
        "queryType": query_type,
        "groupType": group_type,
        "filterDateType": filter_date_type,
        "startDate": start,
        "endDate": end,
        "sortField": "total",
        "sortOrder": "desc",
        "page": page,
        "pageSize": PAGE_SIZE,
        "req_time_sequence": f"/sales-statistics/report/list$${seq}",
    }


def fetch_all(start: str, end: str, query_type: str, group_type: str,
              filter_date_type: str = "year", seq: int = 1) -> tuple[list[dict], int]:
    """经领星 API 服务站一次拉取全量销售统计（登录态由服务端注入，自动翻页）

    filter_date_type: 统计粒度，year=按年区间（默认）；day=按日区间（可用于近30天实抓）
    服务端 auto_pagination 自动翻 page/pageSize；返回 data 即扁平行列表。
    """
    body = _build_payload(1, start, end, query_type, group_type, seq, filter_date_type)
    resp = station_proxy(url=API_URL, body=body, auto_pagination=True)
    collect_raw("sales_statistics", resp, url=API_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[销售统计report/list]调用失败: {resp.get('message')}")
    lst = [it for it in (resp.get("data") or []) if isinstance(it, dict)]
    total = int(resp.get("total") or len(lst))
    return lst, total


def _s(v):
    """任意值 → JSON 字符串（列表/字典/None 均序列化）"""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return str(v)


def _extract(item: dict) -> dict:
    """单条记录 → 全字段落库（标量字段 + JSON字段 + raw_data）"""
    total = item.get("total") or {}
    avg_total = item.get("avg_total") or {}
    return {
        "asin": (item.get("asin") or [{}])[0].get("asin") if isinstance(item.get("asin"), list) and item.get("asin") else None,
        "icon": item.get("icon"),
        "product_name": item.get("product_name"),
        "rel_key": item.get("rel_key"),
        "currency_code": item.get("currency_code"),
        "pic_url": item.get("pic_url"),
        "ps_id": item.get("ps_id"),
        "spu": item.get("spu"),
        "spu_name": item.get("spu_name"),
        "developer": item.get("developer"),
        "seller_principal_usernames": item.get("seller_principal_usernames"),
        "store_type": item.get("store_type"),
        "total_value": total.get("value"),
        "total_survey_value": total.get("survey_value"),
        "avg_total_value": avg_total.get("value"),
        "avg_total_survey_value": avg_total.get("survey_value"),
        # JSON 字段
        "sid_json": _s(item.get("sid")),
        "asin_json": _s(item.get("asin")),
        "bid_json": _s(item.get("bid")),
        "cid_json": _s(item.get("cid")),
        "marketplace_json": _s(item.get("marketplace")),
        "inventory_json": _s(item.get("inventory")),
        "model_json": _s(item.get("model")),
        "seller_name_json": _s(item.get("seller_name")),
        "trend_data_json": _s(item.get("trend_data")),
        "category_text_json": _s(item.get("category_text")),
        "product_brand_text_json": _s(item.get("product_brand_text")),
        "parent_asin_json": _s(item.get("parent_asin")),
        "local_name_json": _s(item.get("local_name")),
        "local_sku_json": _s(item.get("local_sku")),
        "principal_name_json": _s(item.get("principal_name")),
        "local_info_json": _s(item.get("local_info")),
        "global_tags_json": _s(item.get("global_tags")),
        "sid_mskus_json": _s(item.get("sid_mskus")),
        "seller_skus_json": _s(item.get("seller_skus")),
        "seller_info_json": _s(item.get("seller_info")),
        "fnsku_json": _s(item.get("fnsku")),
        "attribute_json": _s(item.get("attribute")),
        "raw_data": json.dumps(item, ensure_ascii=False, default=str),
    }


async def sync_sales_statistics(start: str, end: str, query_type: str, group_type: str,
                                filter_date_type: str = "year", dry_run: bool = False) -> dict:
    """抓取并落库，返回统计（filter_date_type: year=按年；day=按日区间如近30天）"""
    stats = {"total": 0, "pages": 0, "written": 0, "skipped_empty": 0, "errors": []}
    seq = 1
    records = []

    try:
        lst, total = await asyncio.to_thread(
            fetch_all, start, end, query_type, group_type, filter_date_type, seq
        )
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] 抓取失败: {e}")
        stats["errors"].append(str(e))
        return stats

    stats["pages"] = 1
    stats["total"] = total
    print(f"  服务站一次拉取: {len(lst)} 条 (total={total})")

    for item in lst:
        if not isinstance(item, dict):
            continue
        ext = _extract(item)
        if not ext["asin"]:
            stats["skipped_empty"] += 1
            continue
        ext["stat_start_date"] = start
        ext["stat_end_date"] = end
        ext["query_type"] = query_type
        ext["group_type"] = group_type
        ext["fetch_date"] = date.today()
        records.append(ext)

    # 同一 ASIN 可能挂在多个 sid/seller_sku 组合下重复返回：按 ASIN 去重，保留 total 值最大的记录
    best = {}
    for rec in records:
        prev = best.get(rec["asin"])
        if prev is None or int(float(rec["total_value"] or 0) or 0) > int(float(prev["total_value"] or 0) or 0):
            best[rec["asin"]] = rec
    records = list(best.values())
    if len(records) < stats["total"]:
        print(f"  按ASIN去重: {stats['total']} → {len(records)} 条")

    if dry_run:
        print(f"\n[dry-run] 共抓取 {stats['total']} 条（未写库）")
        return stats

    # 幂等落库：同一统计区间先删旧数据，再全量写入
    session = async_session_factory()
    try:
        async with session:
            await session.execute(
                delete(SalesStatisticsReport).where(
                    SalesStatisticsReport.stat_start_date == start,
                    SalesStatisticsReport.stat_end_date == end,
                )
            )
            for rec in records:
                session.add(SalesStatisticsReport(**rec))
            await session.commit()
            stats["written"] = len(records)
            print(f"\n已写入 sales_statistics_reports {len(records)} 条（{start} ~ {end}）")
    finally:
        await session.close()
    await flush_raw()
    return stats


def main_cli():
    parser = argparse.ArgumentParser(description="领星销售统计报表抓取")
    parser.add_argument("--start", type=str, default="",
                        help="统计开始日期 YYYY-MM-DD（默认去年1月1日）")
    parser.add_argument("--end", type=str, default="",
                        help="统计结束日期 YYYY-MM-DD（默认去年12月31日）")
    parser.add_argument("--query-type", type=str, default="volume",
                        help="查询指标类型（volume=销量，默认）")
    parser.add_argument("--group-type", type=str, default="asin",
                        help="分组类型（asin，默认）")
    parser.add_argument("--filter-date-type", type=str, default="year",
                        choices=["year", "day"],
                        help="统计粒度（year=按年区间，day=按日区间，用于近30天实抓）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    args = parser.parse_args()

    if not args.start or not args.end:
        last_year = date.today().year - 1
        args.start = args.start or f"{last_year}-01-01"
        args.end = args.end or f"{last_year}-12-31"

    print(f"抓取销售统计: {args.start} ~ {args.end}, queryType={args.query_type}, groupType={args.group_type}, filterDateType={args.filter_date_type}")
    import asyncio

    stats = asyncio.run(sync_sales_statistics(
        args.start, args.end, args.query_type, args.group_type, args.filter_date_type, args.dry_run
    ))
    if stats["errors"]:
        print(f"\n部分失败: {stats['errors']}")


if __name__ == "__main__":
    main_cli()
