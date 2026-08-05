"""产品等级自动分级脚本（统一数据源：数据库 daily_sales_snapshots 最新快照）

规则（需求文档）：
  老品产品等级：按年销量（快照 thirty_volume 近30天销量 → 年化估算）
  新品产品等级：按预测销量（当前以实际销量为预测参考，后续接入预测模型）
  S级爆款：总销量>5000  计算频率P0
  A级：2000-4999        计算频率P1
  B级：1000-1999        计算频率P2
  C级：300-999          计算频率P3
  D级：1-299            计算频率P4

数据源：daily_sales_snapshots（每日一次快照入库），不实时调用外部接口。

用法: python scripts/assign_product_levels.py [--limit N] [--dry-run]
"""
import argparse
import asyncio
import sys
from datetime import date, timedelta

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select
from app.database import async_session_factory
from app.models.product import Product
from app.models.daily_snapshot import DailySalesSnapshot
from app.services.lifecycle import _map_sales_to_level, LEVEL_FREQUENCY

# 近30天销量 → 年销量估算（30天 × 12）
ANNUALIZE = 12.0


async def get_latest_snapshot_volumes(session, asins: list) -> dict:
    """从数据库最新快照读取每个 ASIN 的近30天销量"""
    latest = (await session.execute(
        select(DailySalesSnapshot.snapshot_date)
        .order_by(DailySalesSnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()
    if latest is None:
        return {}
    rows = (await session.execute(
        select(DailySalesSnapshot.asin, DailySalesSnapshot.thirty_volume)
        .where(DailySalesSnapshot.snapshot_date == latest)
    )).all()
    return {r[0]: (r[1] or 0) for r in rows}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前N个产品(0=全部)")
    parser.add_argument("--dry-run", action="store_true", help="只展示不写库")
    args = parser.parse_args()

    s = async_session_factory()
    try:
        async with s:
            q = select(Product).where(Product.status == True)  # noqa: E712
            if args.limit:
                q = q.limit(args.limit)
            products = (await s.execute(q)).scalars().all()
            print(f"共 {len(products)} 个启用产品，从数据库快照读取销量...")

            # 从数据库读取年销量（近30天销量年化）
            asins = [p.asin for p in products]
            thirty = await get_latest_snapshot_volumes(s, asins)
            volumes = {a: round(thirty.get(a, 0) * ANNUALIZE) for a in asins}
            print(f"数据库快照返回 {len(volumes)} 个 ASIN，未返回的按0处理")

            stats = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
            for p in products:
                vol = volumes.get(p.asin, 0)
                # 新品：预测销量（当前以实际年销量为参考）；老品：年销量
                level = _map_sales_to_level(vol)
                freq = LEVEL_FREQUENCY.get(level, "P4")
                stats[level] = stats.get(level, 0) + 1
                if not args.dry_run:
                    p.product_level = level
                    p.calc_frequency = freq
            if not args.dry_run:
                await s.commit()
                print("已写回 product_level / calc_frequency")

            print("\n等级分布:", stats)

            # 展示 S 级（前10）
            s_list = [(p, volumes.get(p.asin, 0)) for p in products if volumes.get(p.asin, 0) >= 5000]
            s_list.sort(key=lambda x: -x[1])
            print(f"\nS级产品 {len(s_list)} 个，TOP10:")
            for p, vol in s_list[:10]:
                stage = "新品" if (p.list_date and (date.today() - p.list_date).days <= 365) else "老品"
                print(f"  {p.asin} | {p.product_name[:45]} | 年销量~{vol} | {stage} | list={p.list_date}")
    finally:
        await s.close()


asyncio.run(main())
