"""节日日历 API：列表 / 增删改查 / 模板下载 / 表格导入 / 按节日时间点重算生命周期"""

import io
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models.festival_calendar import FestivalCalendar
from app.models.product import Product
from app.schemas.reference import FestivalCalendarCreate, FestivalCalendarResponse, FestivalCalendarUpdate
from app.services.tag_festival import upsert_tag_map

logger = logging.getLogger(__name__)
router = APIRouter()

TEMPLATE_HEADERS = [
    "节日/主题", "亚马逊预计上架开售时间", "节日/主题时间", "预计节日/主题结束时间",
    "启动期", "增长期", "热卖期", "成熟期", "下降期", "备注",
]


def _to_dt(value):
    """转 datetime：支持 datetime/date/数字序列号/常见字符串"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime(1899, 12, 30) + timedelta(days=int(value))
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        if s.isdigit():
            try:
                return datetime(1899, 12, 30) + timedelta(days=int(s))
            except Exception:
                return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def parse_hot_period(period_str) -> tuple:
    """解析热卖期字符串（如 '11月-12月'、'6月'、'10-1月'）→ (start_month, end_month) 或 (None, None)"""
    if not period_str:
        return None, None
    s = str(period_str).strip().replace(" ", "")
    if not s or s in ("-", "None"):
        return None, None
    m = re.match(r"(\d{1,2})\s*[-—–]\s*(\d{1,2})月", s)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"(\d{1,2})月", s)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def _parse_import_rows(rows: list) -> list:
    """解析导入行 → FestivalCalendar 记录字段列表"""
    records = []
    for row in rows:
        if not row or not row[0] or not str(row[0]).strip():
            continue
        festival = str(row[0]).strip()
        hot_start, hot_end = parse_hot_period(row[6] if len(row) > 6 else None)
        records.append({
            "festival": festival,
            "listing_start": _to_dt(row[1] if len(row) > 1 else None),
            "festival_date": _to_dt(row[2] if len(row) > 2 else None),
            "festival_end": _to_dt(row[3] if len(row) > 3 else None),
            "launch_stage": str(row[4]).strip() if len(row) > 4 and row[4] else None,
            "growth_stage": str(row[5]).strip() if len(row) > 5 and row[5] else None,
            "hot_period": str(row[6]).strip() if len(row) > 6 and row[6] else None,
            "hot_start_month": hot_start,
            "hot_end_month": hot_end,
            "mature_stage": str(row[7]).strip() if len(row) > 7 and row[7] else None,
            "decline_stage": str(row[8]).strip() if len(row) > 8 and row[8] else None,
            "notes": str(row[9]).strip() if len(row) > 9 and row[9] else None,
        })
    return records


@router.get("", response_model=List[FestivalCalendarResponse])
async def list_festival_calendar(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
):
    """获取节日日历列表（按节日名称排序）"""
    result = await session.execute(
        select(FestivalCalendar)
        .order_by(FestivalCalendar.festival, FestivalCalendar.id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()


@router.post("", response_model=FestivalCalendarResponse)
async def create_festival_record(
    payload: FestivalCalendarCreate,
    session: AsyncSession = Depends(get_session),
):
    """新增一条节日时间点记录（自动写入同名标签映射 tag_festival_map）"""
    rec = FestivalCalendar(**payload.model_dump())
    session.add(rec)
    await upsert_tag_map(session, rec.festival, rec.festival)
    await session.commit()
    await session.refresh(rec)
    return rec


@router.put("/{record_id}", response_model=FestivalCalendarResponse)
async def update_festival_record(
    record_id: int,
    payload: FestivalCalendarUpdate,
    session: AsyncSession = Depends(get_session),
):
    """更新一条节日时间点记录（节日名变更时自动写入同名标签映射）"""
    rec = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.id == record_id)
    )).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"节日记录 {record_id} 不存在")
    payload_data = payload.model_dump(exclude_unset=True)
    for k, v in payload_data.items():
        setattr(rec, k, v)
    if "festival" in payload_data:
        await upsert_tag_map(session, rec.festival, rec.festival)
    await session.commit()
    await session.refresh(rec)
    return rec


@router.delete("/{record_id}")
async def delete_festival_record(record_id: int, session: AsyncSession = Depends(get_session)):
    """删除一条节日时间点记录"""
    rec = (await session.execute(
        select(FestivalCalendar).where(FestivalCalendar.id == record_id)
    )).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail=f"节日记录 {record_id} 不存在")
    await session.delete(rec)
    await session.commit()
    return {"deleted": record_id}


@router.get("/template")
async def download_template():
    """下载节日时间点导入模板（xlsx）"""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "节日生命周期时间点"
    ws.append(TEMPLATE_HEADERS)
    ws.append(["万圣节", "2026-07-25", "2026-10-31", "2026-10-31", "7月", "8月-9月上旬", "9月中旬-10月上旬", "10月中旬", "10月下旬", "示例行"])
    for col, w in zip("ABCDEFGHIJ", [14, 20, 16, 20, 12, 16, 18, 12, 12, 30]):
        ws.column_dimensions[col].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="festival_lifecycle_template.xlsx"'},
    )


@router.post("/import")
async def import_festival_file(
    file: UploadFile,
    session: AsyncSession = Depends(get_session),
):
    """导入节日时间点 xlsx：按节日覆盖更新；自动修复序列号/缺“月”字，描述性时间用 AI 解析"""
    from openpyxl import load_workbook

    try:
        content = await file.read()
        wb = load_workbook(io.BytesIO(content), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"xlsx 解析失败: {e}") from e

    from app.services.festival_import import import_festival_rows

    result = await import_festival_rows(session, rows, use_ai=True)
    if result["imported"] == 0:
        raise HTTPException(status_code=400, detail="未解析到有效数据行（需含 节日/主题 列）")
    # 导入的节日自动写入同名标签映射（已存在不覆盖）
    for name in result["festivals"]:
        await upsert_tag_map(session, name, name)
    await session.commit()
    return result


@router.post("/apply-lifecycle")
async def apply_festival_lifecycle(
    grades: str = Query("S,A", description="要重算的产品等级，逗号分隔"),
    session: AsyncSession = Depends(get_session),
):
    """按「节日时间点表+当前时间」重算指定等级产品的生命周期，写回 products.life_cycle；
    无节日/无匹配阶段区间的产品跳过（保持原值）"""
    from app.services.festival_lifecycle import lifecycle_by_festival, resolve_festival

    grade_list = [g.strip().upper() for g in grades.split(",") if g.strip()]
    products = (await session.execute(
        select(Product).where(Product.status == True, Product.product_level.in_(grade_list))  # noqa: E712
    )).scalars().all()
    dist: Counter = Counter()
    updated = skipped = 0
    fallback = []
    for p in products:
        fc = await lifecycle_by_festival(p, session)
        if fc:
            dist[fc] += 1
            if p.life_cycle != fc:
                p.life_cycle = fc
                updated += 1
            resolved = await resolve_festival(p, session)
            if resolved and not (p.festival or "").strip():
                p.festival = resolved
        else:
            skipped += 1
            fallback.append(p.asin)
    await session.commit()
    return {
        "total": len(products),
        "matched": len(products) - skipped,
        "updated": updated,
        "fallback": skipped,
        "fallback_asins": fallback,
        "by_lifecycle": dict(dist),
    }


@router.get("/lifecycle-distribution")
async def lifecycle_distribution(
    grades: str = Query("S,A", description="产品等级，逗号分隔"),
    session: AsyncSession = Depends(get_session),
):
    """按节日时间点表实时计算指定等级产品的生命周期分布（不落库）"""
    from app.services.festival_lifecycle import lifecycle_by_festival

    grade_list = [g.strip().upper() for g in grades.split(",") if g.strip()]
    products = (await session.execute(
        select(Product).where(Product.status == True, Product.product_level.in_(grade_list))  # noqa: E712
    )).scalars().all()
    dist: Counter = Counter()
    fallback = 0
    for p in products:
        fc = await lifecycle_by_festival(p, session)
        if fc:
            dist[fc] += 1
        else:
            fallback += 1
    return {
        "total": len(products),
        "by_lifecycle": dict(dist),
        "fallback": fallback,
    }
