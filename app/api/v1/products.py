"""产品管理 API 路由"""

import asyncio
import csv
import io
import json
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import Boolean, Date, DateTime, Float, Integer, Numeric, String, func, or_, select, update

from app.database import get_session
from app.models.product import Product
from app.schemas.product import ProductCreate, ProductUpdate, ProductResponse, ProductPage

router = APIRouter()

# 导出字段标签（与产品列表「字段显隐」范围一致，含 ASIN 维度补充字段）
EXPORT_FIELD_LABELS = {
    "asin": "ASIN", "product_name": "产品名称", "msku": "MSKU", "category": "分类",
    "brand": "品牌", "price": "售价", "thirty_volume": "30天销量", "average_thirty_volume": "30天日均",
    "afn_fulfillable_quantity": "FBA可售", "afn_inbound_shipped_quantity": "在途",
    "rank": "大类排名", "stars": "评分", "reviews_num": "评论数", "list_date": "上架日期",
    "operator": "全部负责人", "primary_operator": "负责人", "product_level": "等级", "life_cycle": "生命周期", "status": "状态",
    "local_sku": "本地SKU", "fnsku": "FNSKU", "parent_asin": "父体ASIN", "product_relation_id": "款名/SPU",
    "amz_product_id": "亚马逊商品ID", "lx_id": "领星ID", "store_id": "店铺ID",
    "listing_title": "亚马逊标题", "local_name": "内部品名", "amz_product_type": "亚马逊产品类型",
    "model": "型号", "remark": "备注",
    "listing_price": "Listing价", "landed_price": "落地价", "regular_price": "日常价", "list_price": "目录价",
    "b2b_price": "B2B价", "fba_fee": "FBA费用", "referral_fee": "佣金", "shipping": "运费",
    "cost_price": "采购报价", "history_price": "历史价格",
    "total_volume": "历史总销量", "yesterday_volume": "昨日销量", "seven_volume": "7天销量",
    "fourteen_volume": "14天销量", "average_seven_volume": "7天日均", "average_fourteen_volume": "14天日均",
    "yesterday_amount": "昨日销售额", "seven_amount": "7天销售额", "fourteen_amount": "14天销售额",
    "thirty_amount": "30天销售额",
    "yesterday_spend": "昨日广告", "seven_spend": "7天广告", "fourteen_spend": "14天广告", "thirty_spend": "30天广告",
    "afn_reserved_quantity": "FBA预留", "reserved_fc_transfers": "待调仓", "reserved_customerorders": "客户订单预留",
    "afn_unsellable_quantity": "不可售", "afn_inbound_working_quantity": "入库中",
    "afn_inbound_receiving_quantity": "待发货",
    "seller_rank": "卖家排名", "category_rank": "类目排名", "small_rank": "小类排名", "seller_category": "卖家类目",
    "open_date_time": "上架时间", "first_order_time": "首单时间", "on_sale_time": "开售时间",
    "create_time": "创建时间", "update_time": "更新时间",
    "brand_name": "品牌名", "category_id": "分类ID", "shop": "店铺", "marketplace": "站点",
    "seller_name": "卖家名称", "fulfillment_channel_type": "配送渠道", "status_text": "状态文本",
    "product_creator_realname": "创建人", "product_developer": "开发人",
    "tags": "标签", "supplier_name": "供应商", "lead_time": "工期(天)", "box_quantity": "单箱数量",
    "min_order_qty": "最低采购", "product_type": "产品类型", "calc_frequency": "计算频率",
    "product_stage": "阶段", "festival": "节日", "core_months": "核心月份", "profit_rate": "利润率",
    "cost_sea_profit": "海运利润$", "cost_sea_margin": "海运毛利率", "cost_basis": "计费口径",
    "calc_date": "计算日期", "calc_score": "最新评分", "calc_base_score": "原始分",
    "calc_level": "决策等级", "calc_qty": "建议量", "calc_inventory_days": "库存天数",
    "calc_cycle": "补货周期", "calc_trigger": "采购触发",
    "inv_snapshot_date": "库存快照日期", "inv_fba_available": "快照FBA可售", "inv_inbound": "快照在途",
    "inv_available_days": "领星可售天数", "inv_estimated_daily_sales": "预估日销",
    "created_at": "创建时间", "updated_at": "更新时间",
}

DEFAULT_EXPORT_FIELDS = ["asin", "product_name", "status", "primary_operator", "product_level",
                         "life_cycle", "product_type", "festival", "shop"]


class CompareAsinsBody(BaseModel):
    """上传 ASIN 列表比对请求体"""
    asins: List[str]


class AsinListActionBody(BaseModel):
    """ASIN 列表操作请求体（行内按钮用，非文件上传）"""
    action: str
    asins: List[str]


def _parse_asins_file(filename: str, content: bytes) -> list:
    """从 txt/csv/xlsx 中解析 ASIN 列表（去重、取10位ASIN）"""
    name = (filename or "").lower()
    raw = []
    if name.endswith((".xlsx", ".xls")):
        import pandas as pd

        df = pd.read_excel(io.BytesIO(content))
        asin_col = None
        for col in df.columns:
            if str(col).strip().upper() in ("ASIN", "ASINS"):
                asin_col = col
                break
        series = df[asin_col] if asin_col is not None else df.iloc[:, 0]
        raw = [str(v) for v in series.tolist()]
    else:
        text = content.decode("utf-8", errors="ignore")
        raw = re.split(r"[\s,;，；\t]+", text)

    result = []
    for token in raw:
        token = (token or "").strip().upper()
        if not token:
            continue
        m = re.search(r"\bB0[A-Z0-9]{8}\b", token)
        if m:
            token = m.group(0)
        if re.fullmatch(r"[A-Z0-9]{10}", token) and token not in result:
            result.append(token)
    return result


def _normalize_asins(asins: list) -> list:
    result = []
    for a in asins or []:
        a = (a or "").strip().upper()
        if a and a not in result:
            result.append(a)
    return result


def _merge_exclude_asins(existing_raw: str, new_asins: list, keep_raw: str = "") -> list:
    """合并排除列表：新 ASIN 去重合并进现有排除列表，保留列表优先级最高（不排除）"""
    keep = set(_normalize_asins(re.split(r"[\s,;，；]+", str(keep_raw or ""))))
    existing = set(_normalize_asins(re.split(r"[\s,;，；]+", str(existing_raw or ""))))
    return sorted(existing | set(_normalize_asins(new_asins)) - keep)


# ── 通用列筛选（列头筛选） ──────────────────────────────
# 派生列（不在 products 表，来自最新计算/库存快照/成本表），列表/导出时用 Python 后置过滤
DERIVED_FIELDS = {
    "calc_date", "calc_score", "calc_base_score", "calc_level", "calc_qty",
    "calc_inventory_days", "calc_cycle", "calc_trigger",
    "inv_snapshot_date", "inv_fba_available", "inv_inbound", "inv_available_days",
    "inv_estimated_daily_sales",
    "cost_sea_profit", "cost_sea_margin", "cost_basis",
}


def _parse_filters(raw: str | None) -> list:
    """解析列筛选 JSON：[{"field","op","value"|"min"|"max"}]，无效字段跳过"""
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    return [f for f in (data if isinstance(data, list) else []) if isinstance(f, dict) and f.get("field")]


def _coerce(col, val):
    """把筛选值按列类型转成可比较类型（日期/布尔/数值），无法转换返回原值"""
    if val in (None, ""):
        return None
    if isinstance(col.type, Date):
        try:
            from datetime import date
            return date.fromisoformat(str(val)[:10])
        except ValueError:
            return None
    if isinstance(col.type, DateTime):
        try:
            from datetime import datetime
            return datetime.fromisoformat(str(val)[:19])
        except ValueError:
            return None
    if isinstance(col.type, Boolean):
        if isinstance(val, bool):
            return val
        return str(val).strip().lower() in ("true", "1", "在售", "是")
    if isinstance(col.type, (Integer, Numeric, Float)):
        try:
            return float(val)
        except (ValueError, TypeError):
            return None
    return val


def _is_date_only(val) -> bool:
    """判断筛选值是否为「仅日期」格式（YYYY-MM-DD）"""
    return bool(re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", str(val or "").strip()))


def _to_float(val):
    """转 float，失败返回 None"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _apply_db_filters(query, filters: list):
    """把列筛选应用到查询（只处理 products 表内字段）"""
    for f in filters or []:
        col = getattr(Product, f.get("field"), None)
        if col is None:
            continue
        op = f.get("op")
        if op == "eq":
            val = _coerce(col, f.get("value"))
            if val is not None:
                query = query.where(col == val)
        elif op == "like":
            keyword = str(f.get("value") or "").strip()
            if keyword:
                query = query.where(col.ilike(f"%{keyword}%"))
        elif op in ("gt", "gte", "lt", "lte"):
            val = _coerce(col, f.get("value"))
            if val is not None:
                query = query.where(getattr(col, {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op])(val))
        elif op == "range":
            mn_raw, mx_raw = f.get("min"), f.get("max")
            if isinstance(col.type, DateTime):
                # 日期型输入解释为「当天 00:00:00 ~ 当天 23:59:59」，避免漏掉结束日当天数据
                mn, mx = _coerce(col, mn_raw), _coerce(col, mx_raw)
                if mn is not None:
                    query = query.where(col >= mn)
                if mx is not None:
                    if _is_date_only(mx_raw):
                        mx = mx.replace(hour=23, minute=59, second=59, microsecond=999999)
                    query = query.where(col <= mx)
            elif isinstance(col.type, String) and (_is_date_only(mn_raw) or _is_date_only(mx_raw)):
                # 原始时间字符串（如 "2026-09-17 23:00:00 -07:00"）按字典序比较，结束日取次日零点前
                mn_s, mx_s = str(mn_raw or "").strip(), str(mx_raw or "").strip()
                if mn_s:
                    query = query.where(col >= mn_s[:10])
                if mx_s:
                    query = query.where(col < _next_day(mx_s[:10]))
            else:
                mn, mx = _coerce(col, mn_raw), _coerce(col, mx_raw)
                if mn is not None:
                    query = query.where(col >= mn)
                if mx is not None:
                    query = query.where(col <= mx)
    return query


def _next_day(day: str) -> str:
    """"YYYY-MM-DD" → 次日同格式字符串（字符串日期列的排他上界）"""
    from datetime import date, timedelta

    try:
        return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    except ValueError:
        return day



def _apply_derived_filters(items: list, filters: list) -> list:
    """派生列（calc_*/inv_*/cost_sea_*）Python 后置过滤"""
    out = items
    for f in filters or []:
        field, op = f.get("field"), f.get("op")
        if op == "like":
            kw = str(f.get("value") or "").lower()
            out = [i for i in out if kw in str(i.get(field) or "").lower()]
        elif op == "eq":
            val = f.get("value")
            if val is not None:
                out = [i for i in out if i.get(field) == val]
        elif op == "range":
            mn, mx = f.get("min"), f.get("max")
            out = [i for i in out if _in_range(i.get(field), mn, mx)]
    return out


def _in_range(v, mn, mx):
    """派生列范围过滤：数值按大小比较，日期等字符串按字典序比较（ISO 字典序即时间序）"""
    if v is None or v == "":
        return False
    lo = None if mn in (None, "") else mn
    hi = None if mx in (None, "") else mx
    num = _to_float(v)
    if num is not None:
        lo_n, hi_n = _to_float(lo), _to_float(hi)
        if (lo is None or lo_n is not None) and (hi is None or hi_n is not None):
            if lo_n is not None and num < lo_n:
                return False
            if hi_n is not None and num > hi_n:
                return False
            return True
    s = str(v)
    for bound, is_min in ((lo, True), (hi, False)):
        if bound is None:
            continue
        b = str(bound)
        # 值带时间而边界只到日时，截断到日再比较（如 "2026-08-04T00:00:00" vs "2026-09-17"）
        if _is_date_only(b):
            s, b = s[:10], b[:10]
        if is_min and s < b:
            return False
        if not is_min and s > b:
            return False
    return True


def _apply_product_filters(
    query,
    keyword: Optional[str],
    status: Optional[bool],
    life_cycle: Optional[str],
    product_level: Optional[str],
    category: Optional[str],
    product_type: Optional[str],
    operator: Optional[str],
    filters: Optional[list] = None,
):
    """产品列表通用筛选条件（列表/导出共用）；filters 为通用列筛选（仅 products 表字段）"""
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(
            or_(
                Product.asin.ilike(like),
                Product.product_name.ilike(like),
                Product.category.ilike(like),
            )
        )
    if status is None:
        query = query.where(Product.status == True)  # noqa: E712
    else:
        query = query.where(Product.status == status)
    if life_cycle:
        query = query.where(Product.life_cycle == life_cycle)
    if product_level:
        query = query.where(Product.product_level == product_level)
    if category:
        query = query.where(Product.category == category)
    if product_type:
        query = query.where(Product.product_type == product_type)
    if operator:
        like = f"%{operator.strip()}%"
        query = query.where(Product.operator.ilike(like))
    if filters:
        query = _apply_db_filters(query, filters)
    return query


async def _build_items(session: AsyncSession, rows: list) -> list:
    """按 ASIN 组装产品列表条目：产品档案 + 成本表当前值 + 最新计算/库存快照补充字段"""
    asins = [p.asin for p in rows]
    if not asins:
        return []

    # 成本表覆盖
    from app.models.product_cost import ProductCost
    from app.services import new_product_policy

    override_map: dict[str, dict] = {}
    if asins:
        cost_rows = (await session.execute(
            select(ProductCost).where(ProductCost.asin.in_(asins))
        )).scalars().all()
        for pc in cost_rows:
            override_map[pc.asin] = {
                k: getattr(pc, k)
                for k in (
                    "price", "cost_cny", "exchange_rate", "length_cm", "width_cm", "height_cm",
                    "weight_kg", "freight_sea_cny", "freight_air_cny", "freight_express_cny",
                    "sorting_fee", "referral_fee", "packing_fee", "inbound_fee", "storage_fee",
                    "ad_fee", "return_loss", "over_threshold_loss", "misc_fee",
                )
                if getattr(pc, k) is not None
            }

    # 最新计算结果（logic_version=1）
    from app.models.calculation import CalculationResult
    from app.models.inventory import InventorySnapshot

    calc_map: dict[str, dict] = {}
    if asins:
        latest_sub = (
            select(CalculationResult.asin, func.max(CalculationResult.calc_date).label("d"))
            .where(CalculationResult.logic_version == 1, CalculationResult.asin.in_(asins))
            .group_by(CalculationResult.asin)
            .subquery()
        )
        calc_rows = (await session.execute(
            select(CalculationResult)
            .join(latest_sub, (CalculationResult.asin == latest_sub.c.asin)
                  & (CalculationResult.calc_date == latest_sub.c.d))
        )).scalars().all()
        calc_map = {
            c.asin: {
                "calc_date": c.calc_date.isoformat() if c.calc_date else None,
                "calc_score": c.purchase_score,
                "calc_base_score": c.base_score,
                "calc_level": c.purchase_level,
                "calc_qty": c.suggested_qty,
                "calc_inventory_days": c.inventory_days,
                "calc_cycle": c.replenishment_cycle,
                "calc_trigger": c.purchase_trigger,
            }
            for c in calc_rows
        }

    # 最新库存快照
    inv_map: dict[str, dict] = {}
    if asins:
        inv_sub = (
            select(InventorySnapshot.asin, func.max(InventorySnapshot.snapshot_date).label("d"))
            .where(InventorySnapshot.asin.in_(asins))
            .group_by(InventorySnapshot.asin)
            .subquery()
        )
        inv_rows = (await session.execute(
            select(InventorySnapshot)
            .join(inv_sub, (InventorySnapshot.asin == inv_sub.c.asin)
                  & (InventorySnapshot.snapshot_date == inv_sub.c.d))
        )).scalars().all()
        inv_map = {
            i.asin: {
                "inv_snapshot_date": i.snapshot_date.isoformat() if i.snapshot_date else None,
                "inv_fba_available": i.fba_available,
                "inv_inbound": (i.fba_inbound or 0) + (i.fba_inbound_shipped or 0),
                "inv_available_days": i.fba_available_days,
                "inv_estimated_daily_sales": i.estimated_daily_sales,
            }
            for i in inv_rows
        }

    items = []
    for p in rows:
        item = ProductResponse.model_validate(p).model_dump()
        try:
            ct = new_product_policy.calc_cost_table(p, override=override_map.get(p.asin))
            sea = (ct.get("channels") or {}).get("sea") or {}
            item["cost_sea_profit"] = sea.get("profit")
            item["cost_sea_margin"] = sea.get("margin")
            item["cost_basis"] = ct.get("freight_basis")
        except Exception:  # noqa: BLE001
            pass
        item.update(calc_map.get(p.asin, {}))
        item.update(inv_map.get(p.asin, {}))
        items.append(item)
    return items


def _asin_item(p: Product) -> dict:
    """ASIN 列表条目（精简字段）"""
    return {
        "asin": p.asin,
        "product_name": p.product_name,
        "category": p.category,
        "operator": p.operator,
        "life_cycle": p.life_cycle,
        "product_level": p.product_level,
        "status": p.status,
        "status_text": p.status_text,
        "in_db": True,
    }


def _asin_item_missing(asin: str) -> dict:
    """保留/排除列表中存在于配置、但库里没有的 ASIN"""
    return {
        "asin": asin, "product_name": None, "category": None, "operator": None,
        "life_cycle": None, "product_level": None, "status": None,
        "status_text": None, "in_db": False,
    }


def _export_value(item: dict, key: str):
    """导出单元格格式化"""
    v = item.get(key)
    if v is None:
        return ""
    if key == "tags":
        if isinstance(v, (list, tuple)):
            return ",".join(str(x) for x in v)
        try:
            arr = json.loads(str(v))
            if isinstance(arr, list):
                return ",".join(str(x) for x in arr)
        except Exception:  # noqa: BLE001
            pass
    if key in ("list_date", "calc_date", "inv_snapshot_date", "open_date_time", "first_order_time",
               "on_sale_time", "create_time", "update_time", "created_at", "updated_at"):
        return str(v)[:10]
    if key == "status":
        return "在售" if v else "停用"
    if key in ("cost_sea_margin", "profit_rate"):
        try:
            return round(float(v) * 100, 2)
        except (ValueError, TypeError):
            return v
    return v


@router.post("/upload-asins")
async def upload_asins(
    file: UploadFile = File(...),
    auto_sync: bool = Query(True, description="存在缺失ASIN时自动触发产品同步"),
    session: AsyncSession = Depends(get_session),
):
    """上传 ASIN 文件（txt/csv/xlsx），与库中产品比对（不看状态）

    缺失的 ASIN 自动触发产品同步，同步完成后用 compare-asins 重新比对。
    """
    content = await file.read()
    asins = _parse_asins_file(file.filename or "", content)
    if not asins:
        raise HTTPException(status_code=400, detail="未识别到有效 ASIN（支持 txt/csv/xlsx）")
    asins = _normalize_asins(asins)

    rows = await session.execute(
        select(Product.asin).where(Product.asin.in_(asins))
    )
    existing = {r[0] for r in rows.all()}
    matched = [a for a in asins if a in existing]
    missing = [a for a in asins if a not in existing]

    sync_triggered = False
    if missing and auto_sync:
        from app.tasks.sync_tasks import sync_products

        asyncio.create_task(sync_products())
        sync_triggered = True

    return {
        "total": len(asins),
        "asins": asins,
        "matched": len(matched),
        "missing": len(missing),
        "missing_asins": missing,
        "sync_triggered": sync_triggered,
    }


@router.post("/compare-asins")
async def compare_asins(
    body: CompareAsinsBody,
    session: AsyncSession = Depends(get_session),
):
    """用上传的 ASIN 列表与当前库中产品重新比对（不看状态），用于同步后复查"""
    asins = _normalize_asins(body.asins)
    if not asins:
        raise HTTPException(status_code=400, detail="ASIN 列表为空")
    rows = await session.execute(
        select(Product.asin).where(Product.asin.in_(asins))
    )
    existing = {r[0] for r in rows.all()}
    matched = [a for a in asins if a in existing]
    missing = [a for a in asins if a not in existing]
    return {
        "total": len(asins),
        "asins": asins,
        "matched": len(matched),
        "missing": len(missing),
        "missing_asins": missing,
        "sync_triggered": False,
    }


@router.get("")
async def list_products(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    keyword: Optional[str] = Query(None, description="按 ASIN/产品名称/分类模糊搜索"),
    status: Optional[bool] = Query(None, description="状态筛选；不传时默认排除已删除/停用产品"),
    life_cycle: Optional[str] = None,
    product_level: Optional[str] = None,
    category: Optional[str] = Query(None, description="按分类筛选"),
    product_type: Optional[str] = Query(None, description="产品类型：节日产品/长期产品"),
    operator: Optional[str] = Query(None, description="按负责人筛选（多个负责人时包含即命中）"),
    filters: Optional[str] = Query(None, description="通用列筛选 JSON：[{field,op,value|min|max}]，op=eq/like/range/gt/gte/lt/lte"),
    session: AsyncSession = Depends(get_session),
):
    """获取产品列表（数据库分页，返回 total+items；默认排除已删除/停用）

    派生列（calc_*/inv_*/cost_sea_*）筛选时改为全量取出后置过滤再分页，保证 total 正确。
    """
    flist = _parse_filters(filters)
    derived = [f for f in flist if f.get("field") in DERIVED_FIELDS]
    base = [f for f in flist if f.get("field") not in DERIVED_FIELDS]
    query = select(Product)
    query = _apply_product_filters(
        query, keyword, status, life_cycle, product_level, category, product_type, operator, base
    )
    if derived:
        rows = (await session.execute(query.order_by(Product.asin))).scalars().all()
        items = await _build_items(session, rows)
        items = _apply_derived_filters(items, derived)
        return {"total": len(items), "items": items[skip:skip + limit]}
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = query.order_by(Product.asin).offset(skip).limit(limit)
    result = await session.execute(query)
    rows = result.scalars().all()
    items = await _build_items(session, rows)
    return {"total": total, "items": items}


@router.get("/export-asins")
async def export_asins(
    keyword: Optional[str] = None,
    status: Optional[bool] = Query(None, description="状态筛选；不传时默认排除已删除/停用产品"),
    life_cycle: Optional[str] = None,
    product_level: Optional[str] = None,
    category: Optional[str] = None,
    product_type: Optional[str] = None,
    operator: Optional[str] = None,
    fields: Optional[str] = Query(None, description="导出字段（逗号分隔，与列表字段显隐范围一致）；不传用默认字段"),
    filters: Optional[str] = Query(None, description="通用列筛选 JSON（与列表一致）"),
    session: AsyncSession = Depends(get_session),
):
    """导出 ASIN 列表 CSV（支持与列表页相同的筛选条件 + 可选字段导出）"""
    from datetime import date

    flist = _parse_filters(filters)
    derived = [f for f in flist if f.get("field") in DERIVED_FIELDS]
    base = [f for f in flist if f.get("field") not in DERIVED_FIELDS]
    query = _apply_product_filters(
        select(Product), keyword, status, life_cycle, product_level, category, product_type, operator, base
    ).order_by(Product.asin)
    rows = (await session.execute(query)).scalars().all()
    items = await _build_items(session, rows)
    if derived:
        items = _apply_derived_filters(items, derived)

    if fields:
        keys = [f.strip() for f in fields.split(",") if f.strip()]
        keys = [k for k in keys if k in EXPORT_FIELD_LABELS]
    else:
        keys = list(DEFAULT_EXPORT_FIELDS)
    if not keys:
        keys = list(DEFAULT_EXPORT_FIELDS)

    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8 BOM
    writer = csv.writer(buf)
    writer.writerow([EXPORT_FIELD_LABELS[k] for k in keys])
    for item in items:
        writer.writerow([_export_value(item, k) for k in keys])
    filename = f"asins_{date.today().isoformat()}.csv"
    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export-exclude-asins")
async def export_exclude_asins(session: AsyncSession = Depends(get_session)):
    """导出当前「不需要的 ASIN 排除列表」（TXT，每行一个，可直接再导入）"""
    from datetime import date

    from app.services import config_service

    raw = await config_service.get_param(session, "listing_exclude_asins") or ""
    asins = _normalize_asins(re.split(r"[\s,;，；]+", str(raw)))
    filename = f"exclude_asins_{date.today().isoformat()}.txt"
    return Response(
        content=("\n".join(asins) + ("\n" if asins else "")).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/exclude-asins")
async def exclude_asins(
    file: UploadFile = File(...),
    trigger_sync: bool = Query(True, description="清洗后自动触发产品重新获取"),
    session: AsyncSession = Depends(get_session),
):
    """上传「不需要的 ASIN 列表」→ 合并写入排除列表 → 库中产品标记已排除 → 可选重新获取数据"""
    from app.services import config_service

    content = await file.read()
    asins = _normalize_asins(_parse_asins_file(file.filename or "", content))
    if not asins:
        raise HTTPException(status_code=400, detail="未识别到有效 ASIN（支持 txt/csv/xlsx）")

    keep_raw = await config_service.get_param(session, "listing_keep_asins") or ""
    old_raw = await config_service.get_param(session, "listing_exclude_asins") or ""
    keep = set(_normalize_asins(re.split(r"[\s,;，；]+", str(keep_raw))))
    to_exclude = set(asins) - keep
    merged = _merge_exclude_asins(old_raw, asins, keep_raw)
    await config_service.set_param(session, "listing_exclude_asins", ",".join(merged))

    # 立即把库中这些 ASIN 标记为已排除（不再参与后续计算/日报）
    updated = 0
    if to_exclude:
        result = await session.execute(
            update(Product)
            .where(Product.asin.in_(sorted(to_exclude)), Product.status == True)  # noqa: E712
            .values(status=False, status_text="已排除")
        )
        updated = result.rowcount or 0
    await session.commit()

    sync_triggered = False
    if trigger_sync and to_exclude:
        from app.tasks.sync_tasks import sync_products

        asyncio.create_task(sync_products())
        sync_triggered = True

    return {
        "total": len(asins),
        "excluded_count": len(to_exclude),
        "kept_count": len(set(asins) & keep),
        "exclude_list_count": len(merged),
        "marked_stopped": updated,
        "sync_triggered": sync_triggered,
    }


@router.post("/keep-asins")
async def keep_asins(
    file: UploadFile = File(...),
    trigger_sync: bool = Query(True, description="恢复后自动触发产品重新获取"),
    session: AsyncSession = Depends(get_session),
):
    """上传「需要的 ASIN 列表」→ 从排除列表移除 → 写入保留列表 → 恢复库中在售状态 → 可选重新获取数据

    用途：之前误排除（或错误加入排除列表）的 ASIN，通过本接口加回。
    优先级：保留列表 > 排除列表（keep 中的 ASIN 不再被排除逻辑处理）。
    """
    from app.services import config_service

    content = await file.read()
    asins = _normalize_asins(_parse_asins_file(file.filename or "", content))
    if not asins:
        raise HTTPException(status_code=400, detail="未识别到有效 ASIN（支持 txt/csv/xlsx）")

    keep_raw = await config_service.get_param(session, "listing_keep_asins") or ""
    exclude_raw = await config_service.get_param(session, "listing_exclude_asins") or ""
    keep_old = set(_normalize_asins(re.split(r"[\s,;，；]+", str(keep_raw))))
    exclude_old = set(_normalize_asins(re.split(r"[\s,;，；]+", str(exclude_raw))))

    to_keep = set(asins)
    # 从排除列表移除
    new_exclude = sorted(exclude_old - to_keep)
    # 合并进保留列表
    new_keep = sorted(keep_old | to_keep)
    await config_service.set_param(session, "listing_exclude_asins", ",".join(new_exclude))
    await config_service.set_param(session, "listing_keep_asins", ",".join(new_keep))

    # 恢复库中这些 ASIN 的在售状态（不参与计算/日报）
    restored = 0
    result = await session.execute(
        update(Product)
        .where(Product.asin.in_(sorted(to_keep)), Product.status == False)  # noqa: E712
        .values(status=True, status_text="在售")
    )
    restored = result.rowcount or 0
    await session.commit()

    sync_triggered = False
    if trigger_sync and to_keep:
        from app.tasks.sync_tasks import sync_products

        asyncio.create_task(sync_products())
        sync_triggered = True

    return {
        "total": len(asins),
        "removed_from_exclude": len(to_keep & exclude_old),
        "keep_list_count": len(new_keep),
        "exclude_list_count": len(new_exclude),
        "restored": restored,
        "sync_triggered": sync_triggered,
    }


@router.get("/asin-list")
async def asin_list(
    tab: str = Query("available", description="available=可用 / all=所有 / keep=保留 / exclude=排除"),
    keyword: Optional[str] = Query(None, description="按 ASIN/产品名称模糊搜索"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    """ASIN 列表（四个子页共用）

    - available：status==True（可用，参与后续分析/日报）
    - all：全表（含被停用/已排除的产品）
    - keep / exclude：读「保留/排除」配置列表，库中不存在的 ASIN 也会列出（in_db=False）
    """
    from app.services import config_service
    from app.models.sync_log import SyncLog

    list_updated_at = (await config_service.get_param(session, "asin_list_updated_at")) or ""
    last_import = (await session.execute(
        select(func.max(SyncLog.completed_at)).where(
            SyncLog.sync_type == "product", SyncLog.status == "success"
        )
    )).scalar()
    last_import_at = last_import.strftime("%Y-%m-%d %H:%M:%S") if last_import else ""

    # 保留/排除列表：数据源是配置，库中无记录的 ASIN 也在列表内
    if tab in ("keep", "exclude"):
        key = "listing_keep_asins" if tab == "keep" else "listing_exclude_asins"
        asins = sorted(config_service.split_asins(await config_service.get_param(session, key)))
        info_map = {}
        if keyword:
            if asins:
                rows = (await session.execute(
                    select(Product).where(Product.asin.in_(asins))
                )).scalars().all()
                info_map = {p.asin: _asin_item(p) for p in rows}
            kw = keyword.strip().upper()
            asins = [
                a for a in asins
                if kw in a.upper()
                or kw in str((info_map.get(a) or {}).get("product_name") or "").upper()
            ]
        total = len(asins)
        page = asins[skip:skip + limit]
        if page and not info_map:
            rows = (await session.execute(
                select(Product).where(Product.asin.in_(page))
            )).scalars().all()
            info_map = {p.asin: _asin_item(p) for p in rows}
        items = [info_map.get(a) or _asin_item_missing(a) for a in page]
        return {"total": total, "items": items,
                "list_updated_at": list_updated_at, "last_import_at": last_import_at}

    query = select(Product)
    if tab != "all":
        query = query.where(Product.status == True)  # noqa: E712
    if keyword:
        like = f"%{keyword.strip()}%"
        query = query.where(or_(Product.asin.ilike(like), Product.product_name.ilike(like)))
    total = (await session.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    rows = (await session.execute(
        query.order_by(Product.asin).offset(skip).limit(limit)
    )).scalars().all()
    return {"total": total, "items": [_asin_item(p) for p in rows],
            "list_updated_at": list_updated_at, "last_import_at": last_import_at}


@router.post("/asin-lists/action")
async def asin_lists_action(
    body: AsinListActionBody,
    session: AsyncSession = Depends(get_session),
):
    """ASIN 列表行内动作（保留/排除/移除），保留优先级高于排除

    action：exclude=加入排除 / keep=加入保留 / remove_exclude=移出排除 / remove_keep=移出保留
    写库后记录操作时间并置「待刷新」，由每 10 分钟任务重新清洗刷新状态。
    """
    from app.services import config_service

    try:
        return await config_service.apply_asin_action(session, body.action, body.asins)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/stats/lifecycle")
async def lifecycle_stats(session: AsyncSession = Depends(get_session)):
    """生命周期与产品等级统计（用于生命周期分析页；仅统计保留下来的启用产品）"""
    total = (
        await session.execute(
            select(func.count()).select_from(Product).where(Product.status == True)  # noqa: E712
        )
    ).scalar() or 0

    life_cycle_rows = await session.execute(
        select(Product.life_cycle, func.count())
        .where(Product.status == True)  # noqa: E712
        .group_by(Product.life_cycle)
    )
    level_rows = await session.execute(
        select(Product.product_level, func.count())
        .where(Product.status == True)  # noqa: E712
        .group_by(Product.product_level)
    )

    return {
        "total": total,
        "by_life_cycle": [{"label": k or "未知", "count": v} for k, v in life_cycle_rows.all()],
        "by_product_level": [{"label": k or "未知", "count": v} for k, v in level_rows.all()],
    }


@router.get("/{asin}", response_model=ProductResponse)
async def get_product(asin: str, session: AsyncSession = Depends(get_session)):
    """获取产品详情"""
    result = await session.execute(select(Product).where(Product.asin == asin))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    return product


@router.post("/", response_model=ProductResponse, status_code=201)
async def create_product(data: ProductCreate, session: AsyncSession = Depends(get_session)):
    """创建产品"""
    product = Product(**data.model_dump())
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@router.put("/{asin}", response_model=ProductResponse)
async def update_product(asin: str, data: ProductUpdate, session: AsyncSession = Depends(get_session)):
    """更新产品信息"""
    result = await session.execute(select(Product).where(Product.asin == asin))
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="产品不存在")
    for key, value in data.model_dump(exclude_unset=True).items():
        setattr(product, key, value)
    await session.commit()
    await session.refresh(product)
    return product
