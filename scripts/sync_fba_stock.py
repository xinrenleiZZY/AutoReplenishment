"""FBA 库存快照同步脚本（领星 query_fba_valid_list 补货建议全量）

数据源：领星 MCP query_fba_valid_list（分页拉取全量 3991 条）
包含：海外仓库存(overseaStock=本地+在途)、总库存、FBA可售天数、断货日期、建议补货量
按 displayInfo.asinList 匹配 ASIN → 写入 inventory_snapshots 表（每日快照，幂等）
同时写回 products 基础数据（afn_* / fba_available_days / stockout_date / estimated_daily_sales），
分析直接读取 products 的最新值，不再依赖旧快照。

字段映射：
  可用库存(可售+预留+在途)  → available_stock  (overseaStock)
  海外仓库存                → local_stock     (overseaStock - fba部分近似)
  FBA可售天数               → fba_available_days (availableSaleDaysFba)
  断货预警日期              → stockout_date
  预计日均销量              → estimated_daily_sales
  建议补货量                → suggest_qty

用法: python scripts/sync_fba_stock.py [--dry-run]
"""
import argparse
import asyncio
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, delete, update
from app.database import async_session_factory
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.services.mcp_client import mcp_call
from app.services.raw_store import flush_raw

PAGE_SIZE = 200
STOCK_LIST_PAGE = 500  # get_fba_stock_list 允许的页大小（20/50/100/200/500/1000/2000/5000）


def fetch_all_stock() -> list:
    """分页拉取领星补货建议全量数据"""
    items = []
    offset = 0
    while True:
        r = mcp_call("lingxing", "query_fba_valid_list", {
            "length": PAGE_SIZE,
            "offset": offset,
        })
        data = r.get("data", {})
        inner = data.get("data", data) if isinstance(data, dict) else data
        lst = inner.get("list", []) if isinstance(inner, dict) else []
        items.extend(lst)
        if len(lst) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return items


def fetch_all_stock_list() -> list:
    """分页拉取领星 FBA 库存列表全量数据（第二通道：含历史可售天数 historical_days_of_supply）"""
    items = []
    offset = 0
    while True:
        r = mcp_call("lingxing", "get_fba_stock_list", {
            "offset": offset,
            "length": STOCK_LIST_PAGE,
            "sort_field": "asc",
            "sort_type": "asc",
            "is_cost_page": "0",
        })
        data = r.get("data", {})
        lst = data.get("list", []) if isinstance(data, dict) else []
        total = int(data.get("total") or 0) if isinstance(data, dict) else 0
        items.extend(lst)
        if len(lst) < STOCK_LIST_PAGE or (total and offset + len(lst) >= total):
            break
        offset += STOCK_LIST_PAGE
    return items


def extract_stock(item: dict) -> list:
    """从单条补货建议提取库存字段（asinList 中每个 ASIN 都映射同一份库存）"""
    d = item.get("displayInfo", {})
    asins = d.get("asinList", []) or []
    if not asins:
        return []
    s = item.get("suggestInfo", {}) or {}
    st = item.get("stockQuantityInfo", {}) or {}
    results = []
    for asin in asins:
        results.append({
            "asin": asin,
            "available_stock": int(float(st.get("overseaStock") or 0)),
            "stock_total": int(float(st.get("stockTotal") or 0)),
            "fba_available_days": _safe_int(s.get("availableSaleDaysFba")),
            "stockout_date": _safe_date(s.get("outStockDate")),
            "estimated_daily_sales": _safe_float(s.get("estimatedSaleAvgQuantity")),
            "suggest_qty": _safe_int(s.get("quantitySugPurchase")),
            "out_stock_flag": _safe_int(s.get("outStockFlag")),
        })
    return results


def extract_stock_list(items: list) -> dict:
    """从 FBA 库存列表聚合库存字段（同一 ASIN 多仓求和/取最大可售天数）"""
    agg: dict = {}
    for it in items:
        a = it.get("asin")
        if not a:
            continue
        g = agg.setdefault(a, {
            "available_stock": 0, "stock_total": 0, "fba_available_days": 0,
            "stockout_date": None, "estimated_daily_sales": 0.0,
            "suggest_qty": 0, "out_stock_flag": 0,
        })
        avail = _safe_int(it.get("available_total"))
        g["available_stock"] += avail
        g["stock_total"] += _safe_int(it.get("total", it.get("available_total")))
        dos = _safe_float(it.get("historical_days_of_supply"))
        if dos > 0:
            g["fba_available_days"] = max(g["fba_available_days"], int(round(dos)))
    return agg


def _safe_int(v, default=0):
    try:
        return int(float(v)) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_date(v):
    """将领星返回的日期字符串(YYYY-MM-DD)转为 date 对象"""
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


async def sync_fba_stock(dry_run: bool = False) -> int:
    """同步 FBA 库存快照（领星 MCP，每日幂等）；返回写入的库存条数"""
    print("开始拉取领星补货建议全量数据...")
    items = fetch_all_stock()
    print(f"领星返回 {len(items)} 条")

    stocks = {}
    for item in items:
        for ext in extract_stock(item):
            if not ext["asin"]:
                continue
            # 同一 ASIN 可能挂在多个组合下：保留可用库存最大的记录
            prev = stocks.get(ext["asin"])
            if prev is None or ext["available_stock"] > prev["available_stock"]:
                stocks[ext["asin"]] = ext
    print(f"解析出 {len(stocks)} 个 ASIN 库存（补货建议通道）")

    # 第二通道：FBA 库存列表（补充历史可售天数，覆盖率更高）
    items2 = fetch_all_stock_list()
    lx_agg = extract_stock_list(items2)
    print(f"FBA库存列表 {len(items2)} 条, 去重 {len(lx_agg)} 个 ASIN")
    # 补货建议缺失可售天数的，用 FBA 库存列表历史可售天数补充
    for asin, st in stocks.items():
        agg = lx_agg.get(asin)
        if agg and not st["fba_available_days"] and agg["fba_available_days"] > 0:
            st["fba_available_days"] = agg["fba_available_days"]
    # 补货建议未覆盖的 ASIN 从 FBA 库存列表补充（可用库存按多仓求和）
    for asin, agg in lx_agg.items():
        if asin not in stocks:
            stocks[asin] = agg
    day_ok = sum(1 for st in stocks.values() if st["fba_available_days"] > 0)
    print(f"合并后 {len(stocks)} 个 ASIN, 其中领星可售天数>0 的有 {day_ok} 个")
    if not stocks:
        raise RuntimeError(
            "领星 FBA 库存同步返回 0 条（补货建议+FBA库存列表均为空），"
            "疑似接口/登录态异常，拒绝写入快照与基础数据"
        )

    s = async_session_factory()
    try:
        async with s:
            if not dry_run:
                # 补齐缺失产品档案（跳过排除列表，避免已删除 ASIN 被重新引入，
                # 如 B0DHTJZ2YC/B0B1X3QJ37/B0D69G91FH 已删除但领星仍有库存）
                asins = list(stocks.keys())
                exist = (await s.execute(select(Product.asin).where(Product.asin.in_(asins)))).scalars().all()
                exist_set = set(exist)
                exclude = set()
                try:
                    from app.services.config_service import get_param
                    raw = await get_param(s, "listing_exclude_asins")
                    for x in str(raw or "").replace("\n", ",").split(","):
                        x = x.strip().upper()
                        if x:
                            exclude.add(x)
                except Exception:  # noqa: BLE001
                    exclude = set()
                missing = [a for a in asins if a not in exist_set and a not in exclude]
                if missing:
                    for a in missing:
                        s.add(Product(asin=a, product_name=f"待补全 {a}", status=True))
                    print(f"补齐缺失产品档案 {len(missing)} 条")
                # 仅处理 products 中已存在的 ASIN（避免外键失败/重复引入）
                stocks = {a: st for a, st in stocks.items() if a in exist_set or a in set(missing)}

                # 写回基础数据 products：任何一次拉取的最新值都落到 ASIN 基础数据，
                # 分析直接用 products，不再读取旧快照
                for asin, st in stocks.items():
                    await s.execute(
                        update(Product)
                        .where(Product.asin == asin)
                        .values(
                            afn_fulfillable_quantity=st["available_stock"],
                            afn_inbound_shipped_quantity=max(0, st["stock_total"] - st["available_stock"]),
                            fba_available_days=st["fba_available_days"] or None,
                            stockout_date=st["stockout_date"],
                            estimated_daily_sales=st["estimated_daily_sales"] or None,
                            updated_at=datetime.now(),
                        )
                    )

            today = date.today()
            # 幂等：清当日旧快照
            await s.execute(delete(InventorySnapshot).where(InventorySnapshot.snapshot_date == today))
            for asin, st in stocks.items():
                s.add(InventorySnapshot(
                    asin=asin,
                    snapshot_date=today,
                    # 口径：fba_available=海外仓可用(overseaStock)；fba_inbound=总库存-海外仓
                    # local_stock 与 fba_available 同源不再重复计数；purchase_on_order 为领星建议补货量，非在途，不计入可用
                    fba_available=st["available_stock"],
                    fba_reserved=0,
                    fba_inbound=st["stock_total"] - st["available_stock"],
                    local_stock=0,
                    purchase_on_order=0,
                    # 领星口径库存天数/售罄日/预估日销（交叉验证用）
                    fba_available_days=st["fba_available_days"],
                    stockout_date=st["stockout_date"],
                    estimated_daily_sales=st["estimated_daily_sales"],
                ))
            await s.commit()
            print(f"已写入 inventory_snapshots {len(stocks)} 条（{today}）")
            await flush_raw()

            # 示例
            for asin in ["B0CXHTDX97", "B01FS7W9MQ"]:
                if asin in stocks:
                    st = stocks[asin]
                    print(f"\n示例 {asin}: 可用={st['available_stock']} 总={st['stock_total']} FBA可售天数={st['fba_available_days']} 断货日={st['stockout_date']} 日均={st['estimated_daily_sales']} 建议补货={st['suggest_qty']}")
            return len(stocks)
    finally:
        await s.close()


def main_cli():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()
    asyncio.run(sync_fba_stock(args.dry_run))


if __name__ == "__main__":
    main_cli()
