# -*- coding: utf-8 -*-
"""
全量产品生命周期识别 + 核心销售月份计算脚本

策略（按计算频率优先级分批，避免 SIF 调用量过大）：
  - S/A/B 级（P0/P1/P2，共143个）：完整 SIF 识别（销量趋势/画像/广告/流量）
  - C/D 级（P3/P4，4100+）：先仅数据库快照规则识别（不调 SIF），后续每日增量
同时从 SIF 月度销量序列计算核心销售月份 core_months（Top N 月）写回 products

用法: python scripts/identify_lifecycle_all.py [--write] [--limit N] [--grades S,A,B]
"""
import argparse
import asyncio
import sys
from datetime import date

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select
from app.database import async_session_factory
from app.models.product import Product
from app.services.lifecycle_sif import identify_with_sif, sif_sales_trend

# 等级 → 优先级顺序
GRADE_ORDER = ["S", "A", "B", "C", "D"]


def calc_core_months(bought: list, dates: list, top_n: int = 3) -> str | None:
    """从 SIF 月度销量序列计算核心销售月份 → '10,11,12' 格式"""
    if not bought or not dates:
        return None
    # 按月聚合：dates[i] → 月份
    month_map = {}
    for d, b in zip(dates, bought):
        try:
            m = int(str(d).split("-")[1])
        except Exception:
            continue
        month_map[m] = month_map.get(m, 0) + int(b)
    if not month_map:
        return None
    top = sorted(month_map.items(), key=lambda x: -x[1])[:top_n]
    return ",".join(str(m) for m, _ in top)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="写回数据库")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量（0=不限）")
    parser.add_argument("--grades", type=str, default="S,A,B", help="要处理的等级，逗号分隔")
    parser.add_argument("--use-sif", action="store_true", help="C/D级也用SIF（慢）")
    args = parser.parse_args()

    grades = [g.strip() for g in args.grades.split(",")]
    s = async_session_factory()
    try:
        async with s:
            rows = (await s.execute(
                select(Product).where(Product.product_level.in_(grades)).order_by(Product.product_level)
            )).scalars().all()
            # 按等级优先级排序
            rows.sort(key=lambda p: GRADE_ORDER.index(p.product_level or "D"))
            if args.limit:
                rows = rows[:args.limit]
            print(f"待识别 {len(rows)} 个产品（等级={grades}）")

            stats = {"S": 0, "A": 0, "B": 0, "C": 0, "D": 0}
            for idx, p in enumerate(rows, 1):
                grade = p.product_level or "D"
                use_sif = (grade in ("S", "A", "B")) or args.use_sif
                try:
                    if use_sif:
                        result = await identify_with_sif(p.asin, p, s)
                    else:
                        # C/D级：仅数据库快照规则（无SIF调用）
                        from app.services.lifecycle_sif import _decide_lifecycle, db_sales_signals
                        db_sig = await db_sales_signals(p.asin, s)
                        signals = dict(db_sig)
                        lifecycle, reason = _decide_lifecycle(p, signals)
                        result = {
                            "lifecycle": lifecycle,
                            "purchase_policy": None,
                            "signals": signals,
                            "reason": reason,
                        }

                    # 核心销售月份（SIF月度序列）
                    core_months = None
                    if use_sif:
                        try:
                            trend = sif_sales_trend(p.asin)
                            core_months = calc_core_months(trend.get("bought", []), trend.get("dates", []))
                        except Exception:
                            pass

                    if args.write:
                        p.life_cycle = result["lifecycle"]
                        p.product_stage = "新品" if (p.list_date and (date.today() - p.list_date).days <= 365) else "老品"
                        if core_months:
                            p.core_months = core_months
                    stats[grade] = stats.get(grade, 0) + 1
                    print(f"[{idx}/{len(rows)}] {p.asin} {grade} → {result['lifecycle']}"
                          + (f" | core_months={core_months}" if core_months else ""))
                except Exception as e:
                    print(f"[{idx}/{len(rows)}] {p.asin} 失败: {e}")

            if args.write:
                await s.commit()
                print(f"\n已写回 {len(rows)} 个产品（{stats}）")
    finally:
        await s.close()


asyncio.run(main())
