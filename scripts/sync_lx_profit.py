"""领星利润回填：按 ASIN 拉取近30天毛利报表，写回 products.profit_rate（毛利率）

用法:
    python scripts/sync_lx_profit.py                # 回填所有 profit_rate 为空的产品
    python scripts/sync_lx_profit.py --limit 50     # 只回填前50个
    python scripts/sync_lx_profit.py --days 90      # 按近90天毛利计算
    python scripts/sync_lx_profit.py --all          # 全量重算（含已有利润率）

数据源：领星 MCP query_order_profit_list_gross_profit（已验证返回 total_sum.gross_margin）
"""

import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from app.services import mcp_client
from app.services.raw_store import flush_raw

logger = logging.getLogger(__name__)


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def parse_gross_margin(resp) -> float | None:
    """解析领星毛利报表响应，返回毛利率（0~1，如 0.2533=25.33%）

    优先 total_sum.gross_margin（ASIN 汇总），缺失时用 list 均值兜底。
    毛利率为 0 视为“期间无有效销量数据”（返回 None 不写库），负毛利（亏损）保留。
    """
    if not isinstance(resp, dict):
        return None
    try:
        data = (resp.get("data") or {}).get("data") or {}
    except AttributeError:
        return None
    total_sum = data.get("total_sum") or {}
    margin = _to_float(total_sum.get("gross_margin"))
    if margin is not None and margin != 0:
        return margin
    items = data.get("list") or []
    margins = [_to_float(i.get("gross_margin")) for i in items if isinstance(i, dict)]
    margins = [m for m in margins if m is not None]
    margins = [m for m in margins if m != 0]
    return round(sum(margins) / len(margins), 4) if margins else None


async def backfill(limit: int, offset: int, days: int, all_: bool) -> dict:
    """回填利润率，返回统计"""
    stats = {"scanned": 0, "updated": 0, "no_data": 0, "failed": 0, "errors": []}
    start = date.today() - timedelta(days=days)
    end = date.today()
    sem = asyncio.Semaphore(5)

    async with async_session_factory() as session:
        q = select(Product).where(Product.status == True)  # noqa: E712
        if not all_:
            q = q.where(Product.profit_rate.is_(None))
        q = q.order_by(Product.asin).offset(offset).limit(limit)
        products = (await session.execute(q)).scalars().all()
        stats["scanned"] = len(products)

        async def fetch(asin: str):
            async with sem:
                try:
                    return await asyncio.wait_for(
                        asyncio.to_thread(mcp_client.lx_gross_profit, asin, start.isoformat(), end.isoformat()),
                        timeout=25,
                    )
                except asyncio.TimeoutError:
                    logger.warning("领星利润超时跳过 %s", asin)
                    return None

        done = 0
        for p in products:
            done += 1
            try:
                resp = await fetch(p.asin)
                if resp is None:
                    stats["no_data"] += 1
                    continue
                margin = parse_gross_margin(resp)
                if margin is None:
                    stats["no_data"] += 1
                    continue
                p.profit_rate = margin
                stats["updated"] += 1
                if done % 50 == 0:
                    await session.commit()  # 分批落库：中断后可从已回填处续传
                    print(f"[{done}/{len(products)}] 进行中: 回填{stats['updated']} 无数据{stats['no_data']} 失败{stats['failed']}")
            except Exception as e:  # noqa: BLE001
                stats["failed"] += 1
                stats["errors"].append(f"{p.asin}: {e}")
                logger.warning("领星利润获取失败 %s: %s", p.asin, e)
        await session.commit()
    await flush_raw()
    return stats


def main():
    parser = argparse.ArgumentParser(description="领星利润回填 profit_rate")
    parser.add_argument("--limit", type=int, default=100000, help="最多处理产品数")
    parser.add_argument("--offset", type=int, default=0, help="跳过前N个产品")
    parser.add_argument("--days", type=int, default=30, help="利润统计天数（默认30）")
    parser.add_argument("--all", action="store_true", help="全量重算（默认只回填为空的产品）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    stats = asyncio.run(backfill(args.limit, args.offset, args.days, args.all))
    print(f"扫描 {stats['scanned']} 个产品 | 回填 {stats['updated']} | 无数据 {stats['no_data']} | 失败 {stats['failed']}")
    for err in stats["errors"][:10]:
        print("  ERROR:", err)


if __name__ == "__main__":
    main()
