"""快照销量 → sales_data 日明细转换脚本

数据源：daily_sales_snapshots 最新快照（30天/14天/昨日汇总）
转换逻辑（区间均摊，data_source='snapshot_split'）：
  昨日（7-27）       = yesterday_volume（真实日销量）
  前2~14天（13天）   = (fourteen_volume - yesterday_volume) / 13
  前15~30天（16天）  = (thirty_volume - fourteen_volume) / 16
  说明：领星API不返回 seven_volume，7天区间无法单独拆，用三段覆盖30天。

注意：这是均摊近似值；每天抓快照累计30天后，
      yesterday_volume 序列即为真实日明细（届时可精确覆盖）。

用法: python scripts/snapshot_to_daily.py [--date 2026-07-28] [--dry-run]
"""
import argparse
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete
from app.database import async_session_factory
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.sales import SalesData


def split_to_daily(snap) -> list:
    """单个快照 → 30天日明细列表 [(date, qty)]"""
    days = []
    snap_date = snap.snapshot_date
    yesterday = snap.yesterday_volume or 0
    fourteen = snap.fourteen_volume or 0
    thirty = snap.thirty_volume or 0

    # 昨日（真实值）
    if yesterday > 0:
        days.append((snap_date - timedelta(days=1), yesterday))

    # 前2~14天（13天）均摊：14天总量减去昨日
    seg14 = max(fourteen - yesterday, 0)
    if seg14 > 0:
        q = seg14 / 13
        for i in range(2, 15):
            days.append((snap_date - timedelta(days=i), q))

    # 前15~30天（16天）均摊：30天总量减去14天
    seg30 = max(thirty - fourteen, 0)
    if seg30 > 0:
        q = seg30 / 16
        for i in range(15, 31):
            days.append((snap_date - timedelta(days=i), q))

    return days


async def snapshot_to_daily(snapshot_date: str | None = None, dry_run: bool = False) -> int:
    """把快照转换为 sales_data 日明细；返回写入的日明细记录数"""
    s = async_session_factory()
    try:
        async with s:
            if snapshot_date:
                snap_date = date.fromisoformat(snapshot_date)
                rows = (await s.execute(
                    select(DailySalesSnapshot).where(DailySalesSnapshot.snapshot_date == snap_date)
                )).scalars().all()
            else:
                latest = (await s.execute(
                    select(DailySalesSnapshot.snapshot_date).order_by(DailySalesSnapshot.snapshot_date.desc()).limit(1)
                )).scalar_one_or_none()
                if latest is None:
                    print("无快照数据")
                    return 0
                rows = (await s.execute(
                    select(DailySalesSnapshot).where(DailySalesSnapshot.snapshot_date == latest)
                )).scalars().all()
            print(f"快照 {rows[0].snapshot_date}: {len(rows)} 个产品")

            total_records = 0
            with_daily = 0
            for snap in rows:
                daily = split_to_daily(snap)
                if daily:
                    with_daily += 1
                    total_records += len(daily)
                    if not dry_run:
                        # 清除该ASIN在快照覆盖区间内的旧近似数据
                        await s.execute(delete(SalesData).where(
                            SalesData.asin == snap.asin,
                            SalesData.data_source == "snapshot_split",
                        ))
                        for d, q in daily:
                            s.add(SalesData(
                                asin=snap.asin,
                                date=d,
                                sales_qty=round(q),
                                data_source="snapshot_split",
                            ))
            if not dry_run:
                await s.commit()
                print(f"已写入 sales_data：{total_records} 条日明细，覆盖 {with_daily} 个ASIN")
            else:
                print(f"预计写入：{total_records} 条日明细，覆盖 {with_daily} 个ASIN")

            # 示例展示
            print("\n示例（B0CXHTDX97）:")
            for snap in rows:
                if snap.asin == "B0CXHTDX97":
                    for d, q in split_to_daily(snap)[:5]:
                        print(f"  {d}: {q:.1f}")
                    break
            return total_records
    finally:
        await s.close()


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", type=str, default=None, help="快照日期 YYYY-MM-DD，默认最新")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(snapshot_to_daily(args.date, args.dry_run))


if __name__ == "__main__":
    main_cli()
