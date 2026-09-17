# -*- coding: utf-8 -*-
"""回填 ASIN 月度历史统计（同比/环比分析数据源）

数据来源:
  领星订单利润报表 lx_profit_report —— 按月查询（销量/销售额/毛利/退货/广告花费/ACOS）

用法: python -m scripts.backfill_monthly_history [--asins A,B,C] [--limit N]
断点续传：已存在的 (asin, month, source) 自动跳过，可反复执行。
注：SIF 回填已停用（历史 sif_bought/sif_price 字段保留，不再写入）。
"""
import asyncio
import sys
from datetime import date, timedelta

sys.path.insert(0, "")

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product
from app.models.historical_monthly import HistoricalMonthlyStats
from app.services import mcp_client as mc

MONTHS_BACK = 14  # 回填最近14个月（覆盖去年同月 + 去年上月 + 上月 + 当前月）


def _month_range(m: str) -> tuple[str, str]:
    y, mo = m.split("-")
    start = f"{y}-{mo}-01"
    if mo == "12":
        end = f"{int(y) + 1}-01-01"
    else:
        end = f"{y}-{int(mo) + 1:02d}-01"
    return start, (date.fromisoformat(end) - timedelta(days=1)).isoformat()


def _months() -> list[str]:
    """最近 MONTHS_BACK 个月（含当前月），格式 YYYY-MM，从旧到新"""
    today = date.today()
    result = []
    y, m = today.year, today.month
    for _ in range(MONTHS_BACK):
        result.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y -= 1
            m = 12
    return result[::-1]


def _f(v, default=None):
    if v in (None, ""):
        return default
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return default


def _parse_lx(total_sum: dict) -> dict | None:
    """解析领星利润报表 total_sum → 月度指标"""
    if not total_sum:
        return None
    volume = _f(total_sum.get("volume"))
    amount = _f(total_sum.get("amount"))
    if (volume or 0) == 0 and (amount or 0) == 0:
        return None  # 无销量月份，跳过
    spend = abs(_f(total_sum.get("spend"), 0.0))
    ad_sales = _f(total_sum.get("ad_sales_amount"))
    acos = (spend / ad_sales) if (ad_sales or 0) > 0 and spend > 0 else None
    return {
        "sale_quantity": int(volume or 0),
        "sale_amount": amount,
        "gross_profit": _f(total_sum.get("gross_profit")),
        "gross_margin": _f(total_sum.get("gross_margin")),
        "return_quantity": int(_f(total_sum.get("return_quantity"), 0.0) or 0),
        "return_rate": _f(total_sum.get("return_rate")),
        "ad_spend": spend,
        "ad_sales": ad_sales,
        "acos": acos,
    }


def _fetch_month(asin: str, m: str) -> tuple[str, dict | None]:
    """同步获取单月领星利润数据（供并发调用）"""
    start, end = _month_range(m)
    try:
        r = mc.lx_profit_report(asin, start, end)
        inner = (r.get("data") or {}).get("data") or {}
        return m, _parse_lx(inner.get("total_sum") or {})
    except Exception as e:
        print(f"  [{asin}] {m} 查询失败: {e}")
        return m, None


async def backfill_lingxing(asin: str, months: list[str], existing: set[str], session) -> int:
    """并发回填领星利润数据（每月1次调用，并发上限8，单次15s超时跳过），返回新增条数"""
    todo = [m for m in months if m not in existing]
    if not todo:
        return 0
    sem = asyncio.Semaphore(8)
    async def _one(m: str):
        async with sem:
            try:
                return await asyncio.wait_for(asyncio.to_thread(_fetch_month, asin, m), timeout=15)
            except asyncio.TimeoutError:
                print(f"  [{asin}] {m} 超时跳过")
                return m, None
    results = await asyncio.gather(*[_one(m) for m in todo])
    added = 0
    for m, parsed in results:
        if not parsed:
            continue
        session.add(HistoricalMonthlyStats(
            asin=asin, month=m, source="lingxing", **parsed,
        ))
        added += 1
    return added


async def run_backfill(limit: int | None = None, asins_arg: list[str] | None = None) -> dict:
    """回填领星月度历史统计并提交，返回各来源新增条数。

    供脚本 CLI 与每日调度复用。
    """
    months = _months()

    async with async_session_factory() as session:
        if asins_arg:
            q = select(Product.asin).where(Product.asin.in_(asins_arg))
        else:
            q = select(Product.asin).where(Product.status == True)  # noqa: E712
        result = await session.execute(q)
        asins = [r[0] for r in result.all()]
        if limit:
            asins = asins[:limit]
        print(f"待回填 ASIN: {len(asins)}，月份: {months[0]}~{months[-1]}")

        added_total = {"lingxing": 0}
        done = 0
        for asin in asins:
            rows = (await session.execute(
                select(HistoricalMonthlyStats).where(HistoricalMonthlyStats.asin == asin)
            )).scalars().all()
            existing = {r.month for r in rows if r.source == "lingxing"}
            n1 = 0
            try:
                n1 = await backfill_lingxing(asin, months, existing, session)
                await session.commit()
            except Exception as e:
                await session.rollback()
                print(f"  [{asin}] 回填失败: {e}")
                continue
            done += 1
            added_total["lingxing"] += n1
            if n1:
                print(f"[{done}/{len(asins)}] {asin} 新增 lingxing={n1}")
            else:
                print(f"[{done}/{len(asins)}] {asin} 无新增（已回填或无数据）")
        print("回填完成")
        return added_total


async def main():
    args = sys.argv[1:]
    limit = None
    asins_arg: list[str] = []
    for i, a in enumerate(args):
        if a == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
        elif a == "--asins" and i + 1 < len(args):
            asins_arg = [x.strip() for x in args[i + 1].split(",") if x.strip()]
    stats = await run_backfill(limit=limit, asins_arg=asins_arg)
    print("统计:", stats)


if __name__ == "__main__":
    asyncio.run(main())
