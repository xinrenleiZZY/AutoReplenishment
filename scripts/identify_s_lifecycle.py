"""对当前 S 级商品（数据库）运行 SIF 生命周期自动识别

数据源：
  1. 产品列表：从数据库读取 product_level='S'（分级脚本已写库）
  2. 生命周期信号：数据库快照优先 + SIF MCP 补充
     （销量增长率/30天销量趋势/广告投入变化/转化率变化/评论增长）

用法: python scripts/identify_s_lifecycle.py [--top N] [--write] [ASIN1 ASIN2 ...]
"""
import argparse
import asyncio
import sys

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select
from app.database import async_session_factory
from app.models.product import Product
from app.services.lifecycle_sif import identify_with_sif


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=3, help="取 S 级前 N 个（按ASIN排序）")
    parser.add_argument("--write", action="store_true", help="将结果写回 product.life_cycle / product_stage")
    parser.add_argument("asins", nargs="*", help="指定 ASIN 列表（优先使用）")
    args = parser.parse_args()

    s = async_session_factory()
    try:
        async with s:
            if args.asins:
                rows = (await s.execute(
                    select(Product).where(Product.asin.in_(args.asins))
                )).scalars().all()
            else:
                rows = (await s.execute(
                    select(Product).where(Product.product_level == "S").order_by(Product.asin)
                )).scalars().all()
                rows = rows[:args.top]
            print(f"待识别 {len(rows)} 个 S 级产品...\n")

            for p in rows:
                print(f"=== {p.asin} | {p.product_name[:45]} | 等级={p.product_level} ===")
                # 综合识别（数据库快照优先 + SIF 信号）
                result = await identify_with_sif(p.asin, p, s)
                print(f"  生命周期: {result['lifecycle']}")
                print(f"  采购策略: {result['purchase_policy']}")
                print(f"  判断依据: {result['reason']}")
                sig = result["signals"]
                if sig:
                    print(f"  信号: {sig}")
                if args.write:
                    p.life_cycle = result["lifecycle"]
                    stage = "新品" if (p.list_date and (__import__('datetime').date.today() - p.list_date).days <= 365) else "老品"
                    p.product_stage = stage
                    print("  → 已写回")
                print()
            if args.write:
                await s.commit()
    finally:
        await s.close()


asyncio.run(main())
