# -*- coding: utf-8 -*-
"""领星经营利润报表毛利率抓取脚本（bd/profit/report/report/asin/list 网页API）

数据源：领星 经营利润报表 API bd/profit/report/report/asin/list（经「领星 API 服务站」转发，登录态由服务端注入）
功能：按单日（startDate=endDate）抓取全部 ASIN 的经营利润报表，写入 profit_report_stats 表。
  - 需求口径：data.records.asins 对应系统 ASIN，data.records.grossRate 即毛利率（0~1，如 0.3806=38.06%）。
  - 分页：接口用 offset/length（非 page/pageSize），服务站 auto_pagination 一次拉全量。
  - 幂等：按 stat_date 先删旧数据再全量写入（每天完整数据入库，每天更新）。
  - 整条原始响应 raw_data 兜底（保证不丢字段）。

用法:
    python scripts/sync_profit_report.py                                # 默认昨天（报表已结算）单日全量
    python scripts/sync_profit_report.py --date 2026-09-01             # 指定单日
    python scripts/sync_profit_report.py --start 2026-09-01 --end 2026-09-03   # 区间逐日回填
    python scripts/sync_profit_report.py --dry-run --date 2026-09-01   # 只抓取不写库
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete

from app.database import async_session_factory
from app.models.profit_report_stat import ProfitReportStat
from app.services.lx_station import station_proxy
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://gw.lingxingerp.com/bd/profit/report/report/asin/list"
PAGE_SIZE = 100       # 接口每页条数（length），auto_pagination 翻页步长
SEQ_START = 1         # req_time_sequence 序号

# 默认公司/市场参数（用户提供的网页接口参数）
DEFAULT_MIDS = [1]
DEFAULT_SIDS = []


def _build_payload(stat_date: str, offset: int, length: int, seq: int,
                   mids: list | None = None, sids: list | None = None) -> dict:
    """按用户提供的接口参数构造请求体（单日：startDate=endDate=stat_date）"""
    return {
        "startDate": stat_date,
        "endDate": stat_date,
        "offset": offset,
        "length": length,
        "mids": mids if mids is not None else DEFAULT_MIDS,
        "sids": sids if sids is not None else DEFAULT_SIDS,
        "currencyCode": "",
        "cids": [],
        "bids": [],
        "principalUids": [],
        "productDeveloperUids": [],
        "searchField": "asin",
        "searchValue": [],
        "sortField": "grossProfit",
        "sortType": "desc",
        "isDisplayByDate": "",
        "version": "",
        "listingTagIds": [],
        "isMonthly": False,
        "orderStatus": "Disbursed",
        "transactionStatus": [],
        "req_time_sequence": f"/bd/profit/report/report/asin/list$${seq}",
    }


def fetch_all_records(stat_date: str, page_size: int = PAGE_SIZE,
                      mids: list | None = None, sids: list | None = None) -> list[dict]:
    """经领星 API 服务站拉取指定日期全部经营利润报表记录（auto_pagination）

    服务端自动注入登录态；data 即扁平记录列表（每条含 asins/grossRate/...）。
    """
    payload = _build_payload(stat_date, 0, page_size, SEQ_START, mids, sids)
    resp = station_proxy(url=API_URL, body=payload, auto_pagination=True)
    collect_raw("profit_report", resp, url=API_URL, method="POST", params=payload)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[经营利润报表]调用失败: {resp.get('message')}")
    return [it for it in (resp.get("data") or []) if isinstance(it, dict)]


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _to_int(v):
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return None


def _extract(item: dict) -> dict:
    """单条记录 → 全字段落库（标量字段 + raw_data）"""
    return {
        "asin": item.get("asins") or item.get("asin"),
        "gross_rate": _to_float(item.get("grossRate")),
        "gross_profit": _to_float(item.get("grossProfit")),
        "roi": _to_float(item.get("roi")),
        "sales_quantity": _to_int(item.get("totalSalesQuantity")),
        "sales_amount": _to_float(item.get("totalSalesAmount")),
        "ads_cost": _to_float(item.get("totalAdsCost")),
        "fba_delivery_fee": _to_float(item.get("totalFbaDeliveryFee")),
        "platform_fee": _to_float(item.get("platformFee")),
        "raw_data": json.dumps(item, ensure_ascii=False, default=str),
    }


async def sync_profit_report(stat_date: str, dry_run: bool = False, page_size: int = PAGE_SIZE,
                             mids: list | None = None, sids: list | None = None) -> dict:
    """抓取指定日期全部 ASIN 经营利润报表并落库，返回统计

    stat_date: 统计单日（YYYY-MM-DD）
    dry_run: 只抓取不写库
    幂等：按 stat_date 先删旧数据再全量写入（每天完整数据入库，每天更新）
    """
    stats = {"date": stat_date, "total": 0, "pages": 0, "written": 0,
             "skipped_empty": 0, "errors": []}
    records = []

    try:
        lst = fetch_all_records(stat_date, page_size, mids, sids)
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {stat_date} 抓取失败: {e}")
        stats["errors"].append(str(e))
        lst = []
    stats["pages"] = 1
    stats["total"] = len(lst)
    print(f"  {stat_date}: {len(lst)} 条")

    for item in lst:
        ext = _extract(item)
        if not ext["asin"]:
            stats["skipped_empty"] += 1
            continue
        ext["stat_date"] = date.fromisoformat(stat_date)
        records.append(ext)

    if dry_run:
        print(f"\n[dry-run] 共抓取 {stats['total']} 条（未写库）")
        return stats

    # 幂等落库：同一统计日期先删旧数据，再全量写入
    session = async_session_factory()
    d = date.fromisoformat(stat_date)
    try:
        async with session:
            await session.execute(
                delete(ProfitReportStat).where(ProfitReportStat.stat_date == d)
            )
            for rec in records:
                session.add(ProfitReportStat(**rec))
            await session.commit()
            stats["written"] = len(records)
            print(f"\n已写入 profit_report_stats {len(records)} 条（{stat_date}）")
    finally:
        await session.close()
    await flush_raw()
    return stats


async def sync_range(start: str, end: str, dry_run: bool = False, page_size: int = PAGE_SIZE,
                     mids: list | None = None, sids: list | None = None) -> dict:
    """区间逐日回填（start ~ end，含首尾），每天全量入库"""
    total_rows = 0
    day = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    errors = []
    while day <= end_d:
        stats = await sync_profit_report(day.isoformat(), dry_run=dry_run,
                                         page_size=page_size, mids=mids, sids=sids)
        total_rows += stats.get("written", 0)
        if stats.get("errors"):
            errors.extend(stats["errors"])
        day += timedelta(days=1)
    return {"start": start, "end": end, "written": total_rows, "errors": errors}


def main_cli():
    parser = argparse.ArgumentParser(description="领星经营利润报表毛利率抓取")
    parser.add_argument("--date", type=str, default="", help="统计单日 YYYY-MM-DD（默认昨天）")
    parser.add_argument("--start", type=str, default="", help="回填开始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", type=str, default="", help="回填结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--page-size", type=int, default=PAGE_SIZE, help="每页条数（默认100）")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写库")
    args = parser.parse_args()

    import asyncio

    if args.start and args.end:
        print(f"区间回填: {args.start} ~ {args.end}, page_size={args.page_size}")
        stats = asyncio.run(sync_range(args.start, args.end, args.dry_run, args.page_size))
    else:
        d = args.date or (date.today() - timedelta(days=1)).isoformat()
        print(f"抓取经营利润报表: {d}, page_size={args.page_size}, dry_run={args.dry_run}")
        stats = asyncio.run(sync_profit_report(d, args.dry_run, args.page_size))
    print(f"\n结果: {stats}")


if __name__ == "__main__":
    main_cli()
