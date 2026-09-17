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
import json
import os
import sys
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete, select

from app.config import settings
from app.database import async_session_factory
from app.models.sales_statistics import SalesStatisticsReport
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://gw.lingxingerp.com/sales-statistics/report/list"
PAGE_SIZE = 500      # 接口支持最大每页500条
REQUEST_INTERVAL = 0.3


def _get_headers() -> dict:
    """领星网页会话接口 headers（优先 config/.env，空则回退内置兜底值）

    可配置项：LX_HEADER_AUTH_TOKEN(auth-token，兼容 LX_AUTH_TOKEN)、
    LX_HEADER_COMPANY_ID(x-ak-company-id)、LX_HEADER_UID(x-ak-uid)、LX_HEADER_ENV_KEY(x-ak-env-key)
    """
    token = settings.LX_HEADER_AUTH_TOKEN or os.getenv("LX_AUTH_TOKEN", "")
    company_id = settings.LX_HEADER_COMPANY_ID or "90136117059997696"
    uid = settings.LX_HEADER_UID or "11054904"
    env_key = settings.LX_HEADER_ENV_KEY or "huizhixin"
    return {
        "accept": "application/json, text/plain, */*",
        "ak-client-type": "web",
        "ak-origin": "https://huizhixin.lingxing.com",
        "auth-token": token,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://huizhixin.lingxing.com",
        "referer": "https://huizhixin.lingxing.com/",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
        "x-ak-company-id": company_id,
        "x-ak-env-key": env_key,
        "x-ak-language": "zh",
        "x-ak-platform": "1",
        "x-ak-request-source": "erp",
        "x-ak-uid": uid,
        "x-ak-version": "3.8.9.3.0.185",
        "x-ak-zid": "1",
    }


def _refresh_token() -> bool:
    """鉴权失败时调用独立模块刷新 token（CDP 自动登录+捕获）"""
    global _HEADERS
    try:
        from browser_api.lingxing_auth import LingxingAuth

        cdp_port = int(os.getenv("LX_CDP_PORT", "18800"))
        token = LingxingAuth(cdp_port=cdp_port).ensure_token()
        if token:
            _HEADERS["auth-token"] = token
            print(f"[INFO] auth-token 已自动刷新: {token[:15]}...")
            return True
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 自动刷新 token 失败: {e}")
    return False


_HEADERS = _get_headers()
_SESSION = requests.Session()


def fetch_page(page: int, start: str, end: str, query_type: str, group_type: str,
               seq: int, filter_date_type: str = "year",
               allow_refresh: bool = True, attempt: int = 1) -> dict:
    """获取一页销售统计数据（网络/5xx 重试 3 次退避；鉴权失败自动刷新 token 重试一次）

    filter_date_type: 统计粒度，year=按年区间（默认）；day=按日区间（可用于近30天实抓）
    """
    payload = {
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
    try:
        resp = _SESSION.post(API_URL, headers=_HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        collect_raw("sales_statistics", data, url=API_URL, method="POST",
                    params=payload, status_code=resp.status_code)
    except (requests.RequestException, ValueError) as e:
        if attempt < 3:
            print(f"[WARN] 第{page}页请求失败(尝试{attempt}/3): {e}，{2 * attempt}s 后重试...")
            time.sleep(2 * attempt)
            return fetch_page(page, start, end, query_type, group_type, seq,
                              filter_date_type, allow_refresh, attempt + 1)
        raise
    if data.get("code") != 1:
        err = f"API error: code={data.get('code')}, msg={data.get('msg')}"
        if allow_refresh and (str(data.get("code")) == "8003" or "鉴权" in str(data.get("msg"))):
            print(f"[WARN] 鉴权失败({err})，尝试自动刷新 token...")
            if _refresh_token():
                return fetch_page(page, start, end, query_type, group_type, seq,
                                  filter_date_type, allow_refresh=False)
        raise Exception(err)
    return data.get("data", {}) or {}


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
    page = 1
    records = []

    while True:
        try:
            data = fetch_page(page, start, end, query_type, group_type, seq, filter_date_type)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] 第{page}页抓取失败: {e}")
            stats["errors"].append(f"page{page}: {e}")
            break
        lst = data.get("list") or []
        stats["pages"] += 1
        stats["total"] += len(lst)
        print(f"  第{page}页: {len(lst)} 条 (count={data.get('count')})")

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

        count = int(data.get("count") or 0)
        offset = int(data.get("offset") or 0)
        if len(lst) < PAGE_SIZE or (count and offset + len(lst) >= count):
            break
        page += 1
        seq += 1
        time.sleep(REQUEST_INTERVAL)

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
