# -*- coding: utf-8 -*-
"""节日时间点导入脚本（自然语言时间解析 → festival_timing 新表）

支持格式（Excel「更新版-产品生命周期时间点2026.7.30.xlsx」实测）：
  - datetime / Excel 序列号 → exact
  - 'X月' → month
  - 'X-Y月' / 'X月-Y月' → month_range
  - 'X月上旬/中旬/下旬'、'X月上旬-中旬' → period（旬）
  - 'X月a-b日'、'X月a日-b日' → day_range
  - '整个X月' → whole_month
  - 'X月末到Y月初'、'X月下旬到Y月初' → period
  - 'X月的第N个星期X' → nth_weekday
  - 'X月的最后一个星期X' → last_weekday
  - 'X月X日（YYYY年）' → exact（带年份）
  - '3月末至4月初的春季复活节会\n5月第二个星期日的母亲节茶会' → 多段解析
  - '每年都不一样'、'每年时间不固定' → unspecified（保留原文）

用法:
  python scripts/import_festival_timing.py [--dry-run]
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

from app.database import async_session_factory, init_db
from app.models.festival_timing import FestivalTiming

XLSX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "docs", "更新版-产品生命周期时间点2026.7.30.xlsx")

# 旬：上旬=1-10日，中旬=11-20日，下旬=21-30日（表头备注）
PART_MAP = {"上": 1, "中": 2, "下": 3}
WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}


def _serial_to_date(v) -> datetime | None:
    """Excel 序列号 → datetime"""
    try:
        return datetime(1899, 12, 30) + timedelta(days=int(v))
    except Exception:
        return None


def _to_dt(v) -> datetime | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        return _serial_to_date(v)
    return None


def _num(s: str):
    """提取字符串中的数字，无则 None"""
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def parse_single(s: str) -> dict | None:
    """解析单个时间段文本 → 结构化 dict；无法解析返回 None"""
    s = s.strip()
    if not s:
        return None
    # 忽略纯说明性短语
    if s in ("每年都不一样", "每年时间不固定"):
        return {"type": "unspecified", "raw": s}
    if s == "全年":
        return {"type": "month_range", "start_month": 1, "end_month": 12, "raw": s}

    # 统一去掉空格（"12 月下旬"、"12 月｜圣诞季"）
    s = re.sub(r"\s+", "", s)

    # 带年份的日期：'2月6日（2027年）'、'2026年3月17日'
    m = re.search(r"(\d{4})年", s)
    year = int(m.group(1)) if m else None

    # 第N个星期X / 最后一个星期X：'5月的第二个星期天' '11月的最后一个星期四' '5月第一个整周'
    m = re.search(r"(\d{1,2})月(?:的)?(第|最后)([一二三四五六日天])个(?:星期|周)([一二三四五六日天])?", s)
    if m:
        month = int(m.group(1))
        if m.group(2) == "最后":
            return {"type": "last_weekday", "month": month, "weekday": WEEKDAY_MAP[m.group(4)] if m.group(4) else 0, "raw": s}
        week = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}.get(m.group(3), 1)
        return {"type": "nth_weekday", "month": month, "week": week, "weekday": WEEKDAY_MAP[m.group(4)] if m.group(4) else 0, "raw": s}
    # '每年5月的第一个星期六'（无"第"字）
    m = re.search(r"(\d{1,2})月的第([一二三四五])个星期([一二三四五六日天])", s)
    if m:
        week = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}[m.group(2)]
        return {"type": "nth_weekday", "month": int(m.group(1)), "week": week,
                "weekday": WEEKDAY_MAP[m.group(3)], "raw": s}
    # 第N周（整周）：'每年4月的第二周' '5月第一个整周' '每年3月的第一个完整周'（可有可无"的"）
    m = re.search(r"(\d{1,2})月(?:的)?第([一二三四五])个?(?:完整)?周", s)
    if m:
        week = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}[m.group(2)]
        return {"type": "nth_week", "month": int(m.group(1)), "week": week, "raw": s}

    # 旬区间：'X月上旬-中旬' 'X月下旬-3月上旬'（跨月）'2上旬-中旬'（无月字）
    m = re.match(r"(\d{1,2})月?([上下中])旬[-–—至到](?:(\d{1,2})月)?([上下中])旬", s)
    if m:
        sm, sp, em, ep = int(m.group(1)), PART_MAP[m.group(2)], m.group(3), m.group(4)
        em = int(em) if em else sm
        return {"type": "period", "start": {"month": sm, "part": sp},
                "end": {"month": em, "part": PART_MAP[ep]}, "raw": s}
    # 单旬：'X月上旬' '2上旬'
    m = re.match(r"(\d{1,2})月?([上下中])旬", s)
    if m:
        p = PART_MAP[m.group(2)]
        return {"type": "period", "start": {"month": int(m.group(1)), "part": p},
                "end": {"month": int(m.group(1)), "part": p}, "raw": s}

    # 月内日期区间：'12月21-25日' '12月26-12月31日' '5月6日-12日' '11月1-2日' '4月11-15日'
    m = re.match(r"(\d{1,2})月(\d{1,2})日?[-–—至到](\d{1,2})日?", s)
    if m:
        return {"type": "day_range", "month": int(m.group(1)), "start_day": int(m.group(2)),
                "end_day": int(m.group(3)), "raw": s}
    # 跨月日期区间：'9月15日到10月15日'
    m = re.match(r"(\d{1,2})月(\d{1,2})日?到(\d{1,2})月(\d{1,2})日?", s)
    if m:
        return {"type": "range", "start_month": int(m.group(1)), "start_day": int(m.group(2)),
                "end_month": int(m.group(3)), "end_day": int(m.group(4)), "raw": s}

    # 精确日期：'X月X日'
    m = re.match(r"(\d{1,2})月(\d{1,2})日?", s)
    if m:
        d = {"type": "exact", "month": int(m.group(1)), "day": int(m.group(2))}
        if year:
            d["year"] = year
        d["raw"] = s
        return d

    # 月末至月初：'3月末至4月初' '9月末到10月初'
    m = re.match(r"(\d{1,2})月(末|下旬)[-–—至到](\d{1,2})月(初|上旬)", s)
    if m:
        return {"type": "period",
                "start": {"month": int(m.group(1)), "part": 3},
                "end": {"month": int(m.group(3)), "part": 1}, "raw": s}

    # 整个X月：'整个2月' '整个3月'
    m = re.match(r"整个(\d{1,2})月", s)
    if m:
        return {"type": "whole_month", "month": int(m.group(1)), "raw": s}

    # 月份区间：'X-Y月' 'X月-Y月' '2-3月' '10-1月' '3–4月' '12月下旬–2月上旬' 已走旬分支
    m = re.match(r"(\d{1,2})月?[-–—至到](\d{1,2})月?", s)
    if m:
        return {"type": "month_range", "start_month": int(m.group(1)), "end_month": int(m.group(2)), "raw": s}

    # 单月：'X月' '2月、3月、11月' 中单个
    m = re.match(r"(\d{1,2})月$", s)
    if m:
        return {"type": "month", "month": int(m.group(1)), "raw": s}

    # 逗号分隔多月：'2月、3月、11月' '9月、11月' 拆多段
    if "、" in s or "，" in s or "," in s:
        parts = re.split(r"[、，,]", s)
        segs = [parse_single(p) for p in parts]
        segs = [x for x in segs if x]
        return segs if len(segs) > 1 else (segs[0] if segs else None)

    return None


def split_segments(text: str) -> list[str]:
    """按换行/分号拆多段（如'3月末至4月初...\n5月第二个星期日的母亲节茶会'）"""
    if not text:
        return []
    raw = str(text).replace("\u3000", " ").strip()
    segs = re.split(r"[\n;；]+", raw)
    return [s.strip() for s in segs if s.strip()]


def parse_time(text) -> list[dict]:
    """解析完整文本 → 结构化 JSON 数组（一段文本可能拆多段/多月）"""
    segs = split_segments(text)
    result: list[dict] = []
    for seg in segs:
        # "或"连接的两个时间（'1月最后一个或2月第一个周末'）拆开分别解析
        if "或" in seg:
            sub_parts = [p.strip() for p in re.split(r"[或]", seg) if p.strip()]
            for sp in sub_parts:
                parsed = parse_single(sp)
                if parsed is None:
                    result.append({"type": "unspecified", "raw": sp})
                elif isinstance(parsed, list):
                    result.extend(parsed)
                else:
                    result.append(parsed)
            continue
        parsed = parse_single(seg)
        if parsed is None:
            # 去括号后再试（'每年3月的第一个完整周（通常 3 月第一周）'）
            cleaned = re.sub(r"[（(].*?[)）]", "", seg)
            if cleaned != seg:
                parsed = parse_single(cleaned)
        if parsed is None:
            # 去掉尾部说明性后缀（'的春季复活节会'、'｜圣诞季'、'，基本持续...'）
            for sep in ("｜", "|", "，", ","):
                if sep in seg:
                    head = seg.split(sep)[0].strip()
                    if head:
                        parsed = parse_single(head)
                        break
        if parsed is None:
            # 数字开头但带说明后缀：'3月末至4月初的春季复活节会' → 截取到"的"前
            if re.match(r"^\d", seg):
                head = re.split(r"[的至到\-–—]", seg)[0]
                # 更精确：截取前一个时间表达式
                m = re.match(r"(\d{1,2}月(?:末|初|下旬|上旬|中旬)?[-–—至到]\d{1,2}月(?:末|初|下旬|上旬|中旬)?|\d{1,2}月(?:末|初|下旬|上旬|中旬)?)", seg)
                if m:
                    parsed = parse_single(m.group(1))
        if parsed is None:
            result.append({"type": "unspecified", "raw": seg})
        elif isinstance(parsed, list):
            result.extend(parsed)
        else:
            result.append(parsed)
    return result


def cell_to_raw(v) -> str | None:
    """单元格 → 原始文本（datetime/序列号转日期字符串）"""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, (int, float)):
        d = _serial_to_date(v)
        return d.strftime("%Y-%m-%d") if d else str(v)
    s = str(v).strip()
    return s if s else None


def parse_cell(v) -> list[dict]:
    """单元格 → 结构化解析"""
    if v is None or str(v).strip() == "":
        return []
    if isinstance(v, datetime) or isinstance(v, (int, float)):
        d = _to_dt(v)
        if d:
            return [{"type": "exact", "year": d.year, "month": d.month, "day": d.day}]
        return []
    return parse_time(v)


async def main(dry_run: bool):
    await init_db()
    wb = load_workbook(XLSX_PATH, data_only=True)
    ws = wb.worksheets[0]

    session = async_session_factory()
    try:
        async with session:
            if not dry_run:
                await session.execute(FestivalTiming.__table__.delete())

            COLUMNS = [
                ("listing_start", 1, "上架时间"),
                ("festival_time", 2, "节日时间"),
                ("festival_end", 3, "结束时间"),
                ("launch", 4, "启动期"),
                ("growth", 5, "增长期"),
                ("hot", 6, "热卖期"),
                ("mature", 7, "成熟期"),
                ("decline", 8, "下降期"),
            ]
            count = 0
            for row in ws.iter_rows(min_row=2, values_only=True):
                if not row or not row[0] or not str(row[0]).strip():
                    continue
                festival = str(row[0]).strip()
                rec = {"festival": festival}
                for col, idx, label in COLUMNS:
                    raw = cell_to_raw(row[idx]) if idx < len(row) else None
                    parsed = parse_cell(row[idx]) if idx < len(row) else []
                    rec[f"{col}_raw"] = raw
                    rec[f"{col}_json"] = json.dumps(parsed, ensure_ascii=False) if parsed else None
                rec["notes"] = str(row[9]).strip() if len(row) > 9 and row[9] else None

                if dry_run:
                    unresolved = [c for c, _, _ in COLUMNS
                                  if rec.get(f"{c}_json") and "unspecified" in rec[f"{c}_json"]]
                    print(f"[{festival}] 未解析: {unresolved if unresolved else '全部解析'}")
                else:
                    session.add(FestivalTiming(**rec))
                count += 1
            if not dry_run:
                await session.commit()
            print(f"\ndry_run={dry_run} 共 {count} 个节日")
    finally:
        await session.close()


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
