# -*- coding: utf-8 -*-
"""
每日销量快照脚本
每天运行一次，抓取每个 ASIN 的销量关键指标，写入数据库 daily_sales_snapshots 表
（统一数据源，供分级/生命周期/预测使用）；同时保留 JSON 文件备份。

用法:
    python scripts/daily_sales_snapshot.py              # 抓取今日快照
    python scripts/daily_sales_snapshot.py --date 2026-07-01  # 指定日期
    python scripts/daily_sales_snapshot.py --no-db       # 只写JSON文件不写库

定时任务（Windows 任务计划程序）:
    每天 09:00 运行: python scripts/daily_sales_snapshot.py

输出:
    p_id/sales_history/YYYY-MM-DD.json  — 每天一个文件（备份）
    daily_sales_snapshots 表            — 统一数据源
"""

import os
import sys
import json
import asyncio
import argparse
from datetime import date, datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from dotenv import load_dotenv
load_dotenv()

from app.database import async_session_factory
from app.models.daily_snapshot import DailySalesSnapshot
from app.services.lx_station import station_proxy
from app.services.raw_store import collect_raw, flush_raw

# ============ 配置 ============
SALES_HISTORY_DIR = os.path.join(BASE_DIR, "p_id", "sales_history")
API_URL = "https://gw.lingxingerp.com/listing-api/api/product/showOnline"
PER_PAGE = 200

# ============ 销量关键字段（从 showOnline 响应中提取） ============
SALES_FIELDS = [
    # 销量指标
    "yesterday_volume",      # 昨日销量
    "seven_volume",          # 近7天销量
    "fourteen_volume",       # 近14天销量
    "thirty_volume",         # 近30天销量
    "total_volume",          # 历史总销量
    "average_seven_volume",  # 近7天日均销量
    "average_fourteen_volume",  # 近14天日均销量
    "average_thirty_volume", # 近30天日均销量
    # 销售额/广告
    "yesterday_amount",      # 昨日销售额
    "seven_amount",          # 近7天销售额
    "thirty_amount",         # 近30天销售额
    "thirty_spend",          # 近30天广告花费
    "seven_spend",           # 近7天广告花费
    # 库存/排名
    "afn_fulfillable_quantity",  # 可售库存
    "afn_reserved_quantity",     # 预留库存
    "afn_inbound_shipped_quantity",  # 在途库存
    "category_rank",         # 类目排名（取数字部分）
]

# ============ 数据抓取（经领星 API 服务站） ============
def _build_payload(offset: int, seq: int) -> dict:
    """构造 showOnline 请求体；offset/length 由服务站 auto_pagination 自动翻页"""
    return {
        "offset": offset,
        "length": PER_PAGE,
        "search_field": "msku",
        "pvi_ids": "",
        "exact_search": 1,
        "sids": "",
        "status": "",
        "is_pair": "",
        "fulfillment_channel_type": "",
        "global_tag_ids": "",
        "req_time_sequence": f"/listing-api/api/product/showOnline$daily${seq}",
    }


def fetch_all(seq: int = 1) -> tuple[list[dict], int]:
    """经服务站一次拉取全量在线产品（登录态由服务端注入，自动翻页）

    服务端 auto_pagination 自动翻 offset/length；返回 data 即产品扁平行列表。
    """
    body = _build_payload(0, seq)
    resp = station_proxy(url=API_URL, body=body, auto_pagination=True)
    collect_raw("showOnline", resp, url=API_URL, method="POST", params=body)
    if not resp.get("success"):
        raise RuntimeError(f"服务站[产品showOnline]调用失败: {resp.get('message')}")
    lst = [it for it in (resp.get("data") or []) if isinstance(it, dict)]
    total = int(resp.get("total") or len(lst))
    return lst, total


def extract_sales_data(item: dict) -> dict:
    """从产品数据中提取销量关键字段"""
    result = {
        "asin": item.get("asin", ""),
        "msku": item.get("msku", ""),
    }
    for field in SALES_FIELDS:
        value = item.get(field)
        # category_rank 可能是 dict，提取 rank 数字
        if field == "category_rank" and isinstance(value, dict):
            value = value.get("rank", 0)
        result[field] = value if value is not None else 0
    return result


def _vol30(d: dict) -> int:
    """取近30天销量（int），供同 ASIN 多 msku 归并时比较"""
    try:
        return int(float((d or {}).get("thirty_volume") or 0))
    except (ValueError, TypeError):
        return 0


def _merge_by_asin(snapshots: dict, asin: str, item: dict):
    """同 ASIN 多 msku（showOnline 按 msku 行返回）→ 同量保大 + 列级非0合并。

    以近30天销量大的一行为基础，基础行为 0 的数值列取其他行的非 0 值合并，
    避免 0 销量 msku 顺序覆盖真实销量行（如 B01FS7W9MQ 挂 12 个 msku，
    仅 colorful fan 有销量，其余 11 个为 0）。
    """
    rec = extract_sales_data(item)
    prev = snapshots.get(asin)
    from utils.asin_merge import merge_asin_records
    snapshots[asin] = merge_asin_records(prev, rec) or rec


def take_snapshot(snapshot_date: str = None, write_db: bool = True):
    """
    获取全量产品销量快照
    snapshot_date: 日期字符串 YYYY-MM-DD，默认今天
    write_db: 是否写入数据库（默认True）
    """
    if snapshot_date is None:
        snapshot_date = date.today().isoformat()

    os.makedirs(SALES_HISTORY_DIR, exist_ok=True)
    output_path = os.path.join(SALES_HISTORY_DIR, f"{snapshot_date}.json")

    # 如果今天已经跑过（文件已存在），跳过
    if os.path.exists(output_path):
        print(f"[{snapshot_date}] 快照已存在: {output_path}")
        print("如需重新抓取，请删除该文件后重试")
        return None, snapshot_date

    print(f"[{snapshot_date}] 开始抓取销量快照...")

    # 服务站一次拉取全量在线产品（登录态由服务端注入）
    items, total = fetch_all(1)
    print(f"  产品总数: {total}, 本次返回: {len(items)} 条")

    snapshots = {}  # ASIN → sales data
    for item in items:
        asin = item.get("asin", "")
        if asin:
            _merge_by_asin(snapshots, asin, item)

    # 写入文件（备份）
    output = {
        "date": snapshot_date,
        "fetch_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_asins": len(snapshots),
        "sales_fields": SALES_FIELDS,
        "data": snapshots,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[{snapshot_date}] 快照完成!")
    print(f"  ASIN数: {len(snapshots)}")
    print(f"  输出: {output_path}")

    # 写入数据库（统一数据源）
    if write_db:
        asyncio.run(save_snapshots_to_db(snapshots, snapshot_date))

    # 汇总统计
    total_vol = sum(int(s.get("yesterday_volume", 0) or 0) for s in snapshots.values())
    print(f"  昨日总销量: {total_vol}")
    return snapshots, snapshot_date


def _to_int(v):
    """字符串/数字 → int，失败返回None"""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _to_float(v):
    """字符串/数字 → float，失败返回None"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


async def save_snapshots_to_db(snapshots: dict, snapshot_date: str):
    """将快照写入 daily_sales_snapshots 表（自动补齐缺失产品档案）"""
    snap_date = date.fromisoformat(snapshot_date)
    session = async_session_factory()
    try:
        async with session:
            from sqlalchemy import select, delete
            from app.models.product import Product

            # 补齐缺失的产品档案（跳过排除列表，避免已删除 ASIN 被重新引入，
            # 如 B0DHTJZ2YC/B0B1X3QJ37/B0D69G91FH 已删除但领星仍有数据）
            exclude = set()
            try:
                from app.services.config_service import get_param
                raw = await get_param(session, "listing_exclude_asins")
                for x in str(raw or "").replace("\n", ",").split(","):
                    x = x.strip().upper()
                    if x:
                        exclude.add(x)
            except Exception:  # noqa: BLE001
                exclude = set()
            exist_rows = await session.execute(select(Product.asin).where(Product.asin.in_(list(snapshots.keys()))))
            exist_asins = set(exist_rows.scalars().all())
            missing = [a for a in snapshots if a not in exist_asins and a not in exclude]
            # 排除列表中的 ASIN 不写入快照（已删除/停用产品）
            snapshots = {a: d for a, d in snapshots.items() if a in exist_asins or a in set(missing)}
            if missing:
                for a in missing:
                    session.add(Product(asin=a, product_name=f"待补全 {a}", status=True))
                await session.flush()
                print(f"  [DB] 补齐缺失产品档案 {len(missing)} 条（已跳过排除列表 {len(exclude)} 个）")

            # 幂等：先清当日旧数据再插入
            await session.execute(delete(DailySalesSnapshot).where(
                DailySalesSnapshot.snapshot_date == snap_date))
            count = 0
            for asin, d in snapshots.items():
                session.add(DailySalesSnapshot(
                    asin=asin,
                    snapshot_date=snap_date,
                    yesterday_volume=_to_int(d.get("yesterday_volume")),
                    seven_volume=_to_int(d.get("seven_volume")),
                    fourteen_volume=_to_int(d.get("fourteen_volume")),
                    thirty_volume=_to_int(d.get("thirty_volume")),
                    total_volume=_to_int(d.get("total_volume")),
                    average_seven_volume=_to_float(d.get("average_seven_volume")),
                    average_fourteen_volume=_to_float(d.get("average_fourteen_volume")),
                    average_thirty_volume=_to_float(d.get("average_thirty_volume")),
                    yesterday_amount=_to_float(d.get("yesterday_amount")),
                    seven_amount=_to_float(d.get("seven_amount")),
                    thirty_amount=_to_float(d.get("thirty_amount")),
                    thirty_spend=_to_float(d.get("thirty_spend")),
                    seven_spend=_to_float(d.get("seven_spend")),
                    afn_fulfillable_quantity=_to_int(d.get("afn_fulfillable_quantity")),
                    afn_reserved_quantity=_to_int(d.get("afn_reserved_quantity")),
                    afn_inbound_shipped_quantity=_to_int(d.get("afn_inbound_shipped_quantity")),
                    category_rank=_to_int(d.get("category_rank")),
                ))
                count += 1
                if count % 500 == 0:
                    await session.flush()
            await session.commit()
            print(f"  [DB] daily_sales_snapshots 写入 {count} 条（{snapshot_date}）")
    finally:
        await session.close()


def import_json_to_db(snapshot_date: str = None):
    """把已有 p_id/sales_history/YYYY-MM-DD.json 导入数据库（不重复抓取）"""
    if snapshot_date is None:
        snapshot_date = date.today().isoformat()
    path = os.path.join(SALES_HISTORY_DIR, f"{snapshot_date}.json")
    if not os.path.exists(path):
        print(f"快照文件不存在: {path}")
        return
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    asyncio.run(save_snapshots_to_db(data.get("data", {}), snapshot_date))


def check_history_status():
    """查看历史快照的累积状态"""
    if not os.path.exists(SALES_HISTORY_DIR):
        print("暂无历史快照数据")
        return

    files = sorted([f for f in os.listdir(SALES_HISTORY_DIR) if f.endswith(".json")])
    if not files:
        print("暂无历史快照数据")
        return

    print(f"\n历史快照 ({len(files)} 天):")
    for f in files[-10:]:  # 最近10天
        path = os.path.join(SALES_HISTORY_DIR, f)
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        total = data.get("total_asins", 0)
        date_str = data.get("date", f.replace(".json", ""))
        print(f"  {date_str}: {total} ASINs")

    if len(files) >= 30:
        print("\n✅ 已满30天，可以计算销量趋势了!")
        print("运行: python scripts/sales_trend.py")
    else:
        print(f"\n⏳ 还需 {30 - len(files)} 天达到30天基准")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="每日销量快照")
    parser.add_argument("--date", type=str, help="指定日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--status", action="store_true", help="查看历史快照状态")
    parser.add_argument("--no-db", action="store_true", help="只写JSON文件不写数据库")
    parser.add_argument("--import-db", action="store_true", help="把已有JSON快照导入数据库（不重新抓取）")
    args = parser.parse_args()

    if args.status:
        check_history_status()
    elif args.import_db:
        import_json_to_db(args.date)
    else:
        take_snapshot(args.date, write_db=not args.no_db)
        asyncio.run(flush_raw())
