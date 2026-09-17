# -*- coding: utf-8 -*-
"""产品成本表：ASIN 维度覆盖字段（展示/维护/Excel导入）

GET  /api/v1/products/{asin}/cost        成本表 + 当前覆盖字段
PUT  /api/v1/products/{asin}/cost        维护覆盖字段（部分更新）
POST /api/v1/products/cost/import        上传 xlsx 模板导入（按 ASIN 匹配填充）
"""

import io
import re
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.product import Product
from app.models.product_cost import ProductCost

router = APIRouter()


class CostUpdate(BaseModel):
    """成本表覆盖字段（可空=清除该字段）"""
    price: Optional[float] = None
    cost_cny: Optional[float] = None
    exchange_rate: Optional[float] = None
    length_cm: Optional[float] = None
    width_cm: Optional[float] = None
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    freight_sea_cny: Optional[float] = None
    freight_air_cny: Optional[float] = None
    freight_express_cny: Optional[float] = None
    sorting_fee: Optional[float] = None
    referral_fee: Optional[float] = None
    packing_fee: Optional[float] = None
    inbound_fee: Optional[float] = None
    storage_fee: Optional[float] = None
    ad_fee: Optional[float] = None
    return_loss: Optional[float] = None
    over_threshold_loss: Optional[float] = None
    misc_fee: Optional[float] = None
    notes: Optional[str] = None

    model_config = {"extra": "ignore"}


def _override_dict(row: ProductCost | None) -> dict:
    if row is None:
        return {}
    return {
        k: getattr(row, k)
        for k in (
            "price", "cost_cny", "exchange_rate", "length_cm", "width_cm", "height_cm",
            "weight_kg", "freight_sea_cny", "freight_air_cny", "freight_express_cny",
            "sorting_fee", "referral_fee", "packing_fee", "inbound_fee", "storage_fee",
            "ad_fee", "return_loss", "over_threshold_loss", "misc_fee", "notes",
        )
        if getattr(row, k) is not None
    }


async def _load_override(session: AsyncSession, asin: str) -> ProductCost | None:
    return (await session.execute(
        select(ProductCost).where(ProductCost.asin == asin)
    )).scalar_one_or_none()


async def _cost_table(asin: str, session: AsyncSession, price_override: float | None = None) -> dict:
    from app.services import new_product_policy
    from app.tasks.calculation_tasks import _get_new_product_cfg

    product = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    row = await _load_override(session, asin)
    ov = _override_dict(row)
    cfg = await _get_new_product_cfg(session)
    table = new_product_policy.calc_cost_table(
        product, cfg=cfg, price_override=price_override, override=ov
    )
    return {
        "asin": asin,
        "overrides": ov,
        "table": table,
    }


@router.get("/{asin}/cost")
async def get_product_cost(asin: str, session: AsyncSession = Depends(get_session)):
    return await _cost_table(asin, session)


@router.put("/{asin}/cost")
async def update_product_cost(asin: str, data: CostUpdate, session: AsyncSession = Depends(get_session)):
    product = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="产品不存在")
    row = await _load_override(session, asin)
    if row is None:
        row = ProductCost(asin=asin)
        session.add(row)
    payload = data.model_dump(exclude_unset=True)
    for k, v in payload.items():
        setattr(row, k, v)
    await session.commit()
    return {"asin": asin, "message": "成本表已保存", "overrides": _override_dict(row)}


def _cell(row: dict, *keys: str) -> float | None:
    """按表头模糊匹配单元格"""
    for k in keys:
        for h, v in row.items():
            if k in str(h):
                if v in (None, ""):
                    return None
                try:
                    return float(str(v).replace(",", "").strip())
                except (TypeError, ValueError):
                    return None
    return None


def _extract_asin(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r"B0[A-Z0-9]{8}", s)
    return m.group(0) if m else (s if re.fullmatch(r"B0[A-Z0-9]{8}", s) else None)


@router.post("/cost/import")
async def import_cost_table(
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """导入成本表 Excel（每行一个 ASIN），按 ASIN 匹配填充覆盖字段"""
    if not (file.filename or "").lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="仅支持 xlsx/xls 文件")
    import pandas as pd

    df = pd.read_excel(io.BytesIO(await file.read()))
    if df.empty:
        raise HTTPException(status_code=400, detail="Excel 为空")
    headers = [str(h) for h in df.columns]
    if not any(("asin" in h.lower() or "链接" in h) for h in headers):
        raise HTTPException(status_code=400, detail="缺少 ASIN 列（ASIN 或 链接/ASIN）")

    matched = 0
    updated = 0
    skipped = 0
    errors = []
    for _, r in df.iterrows():
        row = {str(h): v for h, v in zip(headers, r.tolist())}
        asin = None
        for h, v in row.items():
            if "asin" in h.lower() or "链接" in h:
                asin = _extract_asin(v)
                if asin:
                    break
        if not asin:
            skipped += 1
            continue
        exists = (await session.execute(
            select(Product).where(Product.asin == asin)
        )).scalar_one_or_none()
        if exists is None:
            skipped += 1
            continue
        matched += 1
        pc = await _load_override(session, asin)
        if pc is None:
            pc = ProductCost(asin=asin)
            session.add(pc)
        fields = {
            "price": _cell(row, "售价", "PRICE", "price"),
            "cost_cny": _cell(row, "采购总成本", "大货单价", "采购成本"),
            "exchange_rate": _cell(row, "汇率"),
            "length_cm": _cell(row, "包装长", "长cm", "长度cm"),
            "width_cm": _cell(row, "包装宽", "宽cm", "宽度cm"),
            "height_cm": _cell(row, "包装高", "高cm", "高度cm"),
            "weight_kg": _cell(row, "重量kg", "毛重kg", "重量(kg)"),
            "freight_sea_cny": _cell(row, "海运运费", "海运"),
            "freight_air_cny": _cell(row, "空派运费", "空派"),
            "freight_express_cny": _cell(row, "快递运费", "快递"),
            "sorting_fee": _cell(row, "分拣费", "FBA费"),
            "referral_fee": _cell(row, "佣金"),
            "packing_fee": _cell(row, "P卡"),
            "inbound_fee": _cell(row, "入库配置费"),
            "storage_fee": _cell(row, "仓储费"),
            "ad_fee": _cell(row, "广告费"),
            "return_loss": _cell(row, "退货成本"),
            "over_threshold_loss": _cell(row, "超阈值"),
            "misc_fee": _cell(row, "附加费"),
        }
        has_value = any(v is not None for v in fields.values())
        if not has_value:
            skipped += 1
            continue
        for k, v in fields.items():
            if v is not None:
                setattr(pc, k, v)
        updated += 1
    await session.commit()
    return {
        "message": f"导入完成：匹配 {matched}，填充 {updated}，跳过 {skipped}",
        "matched": matched,
        "updated": updated,
        "skipped": skipped,
        "errors": errors[:10],
    }
