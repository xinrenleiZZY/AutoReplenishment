# -*- coding: utf-8 -*-
"""ACOS 回填：领星利润报表/历史月度表 → products.acos_30d

目的：批量计算时 _fetch_acos 优先读 products.acos_30d，避免每个产品逐个调外部接口。
数据源优先级：
  1. historical_monthly_stats（领星利润报表按月回填，acos=广告花费/广告销售额，无需外部调用）
  2. 领星 query_order_profit_list 近30天 total_sum（spend / ad_sales_amount）

用法:
  python -m scripts.sync_acos                # 回填 acos_30d 为空的产品
  python -m scripts.sync_acos --limit 50     # 只处理前50个
  python -m scripts.sync_acos --all          # 全量重算
"""
import argparse
import asyncio
import logging
from datetime import date, timedelta

from sqlalchemy import case, select

from app.database import async_session_factory
from app.models.historical_monthly import HistoricalMonthlyStats
from app.models.product import Product
from app.services.mcp_client import lx_profit_report
from app.services.raw_store import flush_raw

logger = logging.getLogger(__name__)


def _f(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _parse_acos(resp) -> float | None:
    """从领星利润报表响应解析 ACOS = 广告花费 / 广告销售额"""
    try:
        total_sum = ((resp or {}).get("data") or {}).get("data") or {}
        total_sum = total_sum.get("total_sum") or {}
    except AttributeError:
        return None
    spend = _f(total_sum.get("spend"))
    ad_sales = _f(total_sum.get("ad_sales_amount"))
    if spend and ad_sales and spend > 0 and ad_sales > 0:
        return round(spend / ad_sales, 4)
    return None


async def backfill(limit: int, offset: int, all_: bool) -> dict:
    """回填 ACOS，返回统计"""
    stats = {"scanned": 0, "updated": 0, "no_data": 0, "failed": 0, "errors": []}
    sem = asyncio.Semaphore(6)

    async with async_session_factory() as session:
        # 第一优先：历史月度表（无需外部调用）
        if not all_:
            rows = (await session.execute(
                select(HistoricalMonthlyStats.asin, HistoricalMonthlyStats.acos)
                .where(HistoricalMonthlyStats.source == "lingxing",
                       HistoricalMonthlyStats.acos.isnot(None))
                .order_by(HistoricalMonthlyStats.month.desc())
            )).all()
            acos_map: dict[str, float] = {}
            for asin, acos in rows:
                acos_map.setdefault(asin, float(acos))
            if acos_map:
                result = await session.execute(
                    Product.__table__.update()
                    .where(Product.asin.in_(list(acos_map.keys())), Product.acos_30d.is_(None))
                    .values(acos_30d=case(
                        *[(Product.asin == a, v) for a, v in acos_map.items()],
                        else_=None,
                    ))
                )
                await session.commit()
                stats["updated"] += result.rowcount
                print(f"历史月度表回填 ACOS: {result.rowcount} 个")

        q = select(Product).where(Product.status == True)  # noqa: E712
        if not all_:
            q = q.where(Product.acos_30d.is_(None))
        q = q.order_by(Product.asin).offset(offset).limit(limit)
        products = (await session.execute(q)).scalars().all()
        stats["scanned"] = len(products)

        async def fetch(asin: str):
            async with sem:
                try:
                    start = (date.today() - timedelta(days=30)).isoformat()
                    end = date.today().isoformat()
                    return await asyncio.wait_for(
                        asyncio.to_thread(lx_profit_report, asin, start, end),
                        timeout=25,
                    )
                except asyncio.TimeoutError:
                    logger.warning("领星 ACOS 超时跳过 %s", asin)
                    return None

        done = 0
        for p in products:
            done += 1
            try:
                resp = await fetch(p.asin)
                if resp is None:
                    stats["no_data"] += 1
                    continue
                acos = _parse_acos(resp)
                if acos is None:
                    stats["no_data"] += 1
                    continue
                p.acos_30d = acos
                stats["updated"] += 1
                if done % 50 == 0:
                    await session.commit()
                    print(f"[{done}/{len(products)}] 进行中: 回填{stats['updated']} 无数据{stats['no_data']} 失败{stats['failed']}")
            except Exception as e:  # noqa: BLE001
                stats["failed"] += 1
                stats["errors"].append(f"{p.asin}: {e}")
                logger.warning("领星 ACOS 获取失败 %s: %s", p.asin, e)
        await session.commit()
    await flush_raw()
    return stats


def main():
    parser = argparse.ArgumentParser(description="ACOS 回填 acos_30d")
    parser.add_argument("--limit", type=int, default=100000)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--all", action="store_true", help="全量重算（默认只回填为空的产品）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    stats = asyncio.run(backfill(args.limit, args.offset, args.all))
    print(f"扫描 {stats['scanned']} 个产品 | 回填 {stats['updated']} | 无数据 {stats['no_data']} | 失败 {stats['failed']}")
    for err in stats["errors"][:10]:
        print("  ERROR:", err)


if __name__ == "__main__":
    main()
