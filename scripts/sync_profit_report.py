# -*- coding: utf-8 -*-
"""领星经营利润报表毛利率抓取脚本（bd/profit/report/report/asin/list 网页API）

数据源：领星网页 API https://gw.lingxingerp.com/bd/profit/report/report/asin/list
功能：按单日（startDate=endDate）抓取全部 ASIN 的经营利润报表，写入 profit_report_stats 表。
  - 需求口径：data.records.asins 对应系统 ASIN，data.records.grossRate 即毛利率（0~1，如 0.3806=38.06%）。
  - 分页：接口用 offset/length（非 page/pageSize），响应 data.total 为总条数。
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
import time
from datetime import date, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv()

from sqlalchemy import delete

from app.config import settings
from app.database import async_session_factory
from app.models.profit_report_stat import ProfitReportStat
from app.services.raw_store import collect_raw, flush_raw

API_URL = "https://gw.lingxingerp.com/bd/profit/report/report/asin/list"
PAGE_SIZE = 100       # 接口每页条数（length），可按需调整
REQUEST_INTERVAL = 0.3
SEQ_START = 1         # req_time_sequence 序号

# 默认公司/市场参数（用户提供的网页接口参数）
DEFAULT_MIDS = [1]
DEFAULT_SIDS = []


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
        "x-ak-version": "3.9.0.3.0.089",
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


def fetch_report(stat_date: str, offset: int, length: int, seq: int,
                 mids: list | None = None, sids: list | None = None,
                 allow_refresh: bool = True, attempt: int = 1) -> dict:
    """获取一页经营利润报表数据（网络/5xx 重试 3 次退避；鉴权失败自动刷新 token 重试一次）

    返回 data 子对象：{records: [...], total: N}。异常时抛 Exception。
    """
    payload = _build_payload(stat_date, offset, length, seq, mids, sids)
    try:
        resp = _SESSION.post(API_URL, headers=_HEADERS, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        collect_raw("profit_report", data, url=API_URL, method="POST",
                    params=payload, status_code=resp.status_code)
    except (requests.RequestException, ValueError) as e:
        if attempt < 3:
            print(f"[WARN] offset={offset} 请求失败(尝试{attempt}/3): {e}，{2 * attempt}s 后重试...")
            time.sleep(2 * attempt)
            return fetch_report(stat_date, offset, length, seq, mids, sids,
                                allow_refresh, attempt + 1)
        raise
    if data.get("code") != 1:
        err = f"API error: code={data.get('code')}, msg={data.get('msg')}"
        if allow_refresh and (str(data.get("code")) == "8003" or "鉴权" in str(data.get("msg"))):
            print(f"[WARN] 鉴权失败({err})，尝试自动刷新 token...")
            if _refresh_token():
                return fetch_report(stat_date, offset, length, seq, mids, sids,
                                    allow_refresh=False)
        raise Exception(err)
    return data.get("data", {}) or {}


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
    seq = SEQ_START
    offset = 0
    records = []

    while True:
        try:
            data = fetch_report(stat_date, offset, page_size, seq, mids, sids)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR] offset={offset} 抓取失败: {e}")
            stats["errors"].append(f"offset{offset}: {e}")
            break
        lst = data.get("records") or []
        stats["pages"] += 1
        stats["total"] += len(lst)
        total = int(data.get("total") or 0)
        print(f"  offset={offset}: {len(lst)} 条 (total={total})")

        for item in lst:
            if not isinstance(item, dict):
                continue
            ext = _extract(item)
            if not ext["asin"]:
                stats["skipped_empty"] += 1
                continue
            ext["stat_date"] = date.fromisoformat(stat_date)
            records.append(ext)

        offset += len(lst)
        seq += 1
        # 终止条件：本页返回不足一页，或已遍历完 total
        if len(lst) < page_size or (total and offset >= total):
            break
        time.sleep(REQUEST_INTERVAL)

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
