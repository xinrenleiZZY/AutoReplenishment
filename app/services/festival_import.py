# -*- coding: utf-8 -*-
"""节日时间点表导入（规则修复 + AI 解析描述性时间）

处理三类非纯时间内容：
  1. Excel 日期序列号（如 46023）→ 转成「月+旬」文字；
  2. 缺“月”字的写法（如 2上旬-中旬）→ 补全；
  3. 描述性时间（如「3月末至4月初的春季复活节会」「5月第二个星期日的母亲节茶会」）
     → 用 AI 解析为具体日期/月旬区间。
"""
import json
import logging
import re
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.festival_calendar import FestivalCalendar
from app.services.festival_lifecycle import parse_stage

logger = logging.getLogger(__name__)

_XUN = {"上": (1, 10), "中": (11, 20), "下": (21, 31)}


def _to_dt(value):
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


def _serial_to_date(value) -> date | None:
    try:
        return (datetime(1899, 12, 30) + timedelta(days=int(str(value).strip()))).date()
    except Exception:
        return None


def _date_to_stage_text(d: date) -> str:
    for xun, (ds, de) in _XUN.items():
        if ds <= d.day <= de:
            return f"{d.month}月{xun}旬"
    return f"{d.month}月{d.day}日"


def _extract_dates_from_text(text: str, year: int) -> list:
    """从描述文本提取具体日期（支持 2027年2月9日 / 2月9日（2027年） / 3月28日（2027年））"""
    out = []
    if not text:
        return out
    for m in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", text):
        out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日[（(](\d{4})年[)）]", text):
        out.append(date(int(m.group(3)), int(m.group(1)), int(m.group(2))))
    return out


def normalize_stage_text(text: str) -> str:
    """规则修复：序列号→月旬；缺“月”字补全；其余原样返回"""
    s = str(text).strip()
    if s.isdigit() and len(s) >= 4:
        d = _serial_to_date(s)
        if d:
            return _date_to_stage_text(d)
        return s
    fixed = re.sub(r"^(\d{1,2})(上|中|下)旬", r"\1月\2旬", s)
    if fixed != s and parse_stage(fixed):
        return fixed
    return s


async def ai_extract_stage(text: str, festival: str, stage_name: str) -> str | None:
    """AI 把描述性文本转成标准「月/旬」区间（如 3月末至4月初 → 3月下旬-4月上旬）"""
    if not text or str(text).strip() in ("全年", "-", "None"):
        return None
    try:
        from app.services.ai_eval import chat_completion

        messages = [
            {"role": "system", "content": (
                "你是亚马逊节日销售时间分析助手。把自然语言中的时间描述转换成标准格式："
                "月+旬写法（上旬=1-10日，中旬=11-20日，下旬=21-30日），"
                "支持区间如「8月-9月上旬」「9月中旬-10月上旬」「12月21-25日」「11-12月」。"
                "只输出 JSON：{\"value\": \"转换后的时间\"}；无法确定输出 {\"value\": null}。"
            )},
            {"role": "user", "content": f"节日/主题：{festival}；阶段：{stage_name}；原文：{text}"},
        ]
        resp = await chat_completion(messages, temperature=0, max_tokens=300)
        data = json.loads(resp)
        val = (data.get("value") or "").strip()
        if val and parse_stage(val):
            return val
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 解析阶段时间失败 %s/%s/%s: %s", festival, stage_name, text, e)
    return None


async def ai_extract_periods(text: str, festival: str, field: str, year: int) -> list | None:
    """AI 从描述性文本提取时间段列表（支持一个或多个时间段）

    返回 [{start, end, label}]，start/end 为 YYYY-MM-DD；
    覆盖：单天、整个月份、区间、每年浮动规则、多事件描述。
    """
    if not text:
        return None
    try:
        from app.services.ai_eval import chat_completion

        messages = [
            {"role": "system", "content": (
                f"你是节日日期分析助手。把时间描述转换成一个或多个具体时间段，"
                f"参考年份优先 {year}，若文本含明确年份（如 2027年）则用该年份。"
                "覆盖写法：单天（2月14日）、整个月份（整个3月）、区间（9月15日到10月15日、4-8月）、"
                "浮动规则（每年5月的第二个星期天、每年3月的第一个完整周、1月最后一个或2月第一个周末）、"
                "多事件（如复活节会+母亲节茶会，需输出多个时间段）。"
                "跨年份的季节区间（如 12月-1月）按 {year} 年起算，12月段落在 {year}，1月段落在 {year+1}。"
                "每个时间段输出 start/end（YYYY-MM-DD）与 label（事件名，可为空）；单天 start=end。"
                "只输出 JSON：{\"periods\": [{\"start\": \"YYYY-MM-DD\", \"end\": \"YYYY-MM-DD\", \"label\": \"\"}]}；"
                "无法确定（如 每年都不一样、时间不固定）输出 {\"periods\": []}。"
            )},
            {"role": "user", "content": f"节日/主题：{festival}；字段：{field}；原文：{text}"},
        ]
        resp = await chat_completion(messages, temperature=0, max_tokens=600)
        data = json.loads(resp)
        periods = data.get("periods") or []
        out = []
        for p in periods:
            start = str(p.get("start") or "").strip()
            end = str(p.get("end") or start).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", end):
                out.append({"start": start, "end": end, "label": str(p.get("label") or "").strip()})
        return out if out else None
    except Exception as e:  # noqa: BLE001
        logger.warning("AI 解析时间段失败 %s/%s/%s: %s", festival, field, text, e)
    return None


async def parse_row_to_record(row: tuple, year: int, use_ai: bool = True) -> dict | None:
    """解析单行 → 节日时间点记录（含规则修复与 AI 解析）"""
    if not row or not row[0] or not str(row[0]).strip():
        return None
    festival = str(row[0]).strip()
    record = {
        "festival": festival,
        "listing_start": _to_dt(row[1] if len(row) > 1 else None),
        "festival_date": _to_dt(row[2] if len(row) > 2 else None),
        "festival_end": _to_dt(row[3] if len(row) > 3 else None),
        "launch_stage": str(row[4]).strip() if len(row) > 4 and row[4] else None,
        "growth_stage": str(row[5]).strip() if len(row) > 5 and row[5] else None,
        "hot_period": str(row[6]).strip() if len(row) > 6 and row[6] else None,
        "mature_stage": str(row[7]).strip() if len(row) > 7 and row[7] else None,
        "decline_stage": str(row[8]).strip() if len(row) > 8 and row[8] else None,
        "notes": str(row[9]).strip() if len(row) > 9 and row[9] else None,
    }
    # 热卖期起止月份（hot_start_month/hot_end_month）由热卖期文本解析
    hot_start, hot_end = parse_hot_period(record["hot_period"])
    record["hot_start_month"] = hot_start
    record["hot_end_month"] = hot_end

    # 1) 阶段列：规则修复（序列号/补“月”字），无法解析且含描述文字 → AI 解析
    for field in ("launch_stage", "growth_stage", "hot_period", "mature_stage", "decline_stage"):
        raw = record[field]
        if raw is None or raw in ("", "-", "None"):
            continue
        normalized = normalize_stage_text(raw)
        if parse_stage(normalized):
            record[field] = normalized
            continue
        if use_ai and normalized not in ("全年",):
            ai_val = await ai_extract_stage(normalized, festival, field)
            if ai_val:
                record[field] = ai_val
    # 全年 → 备注标记为长期产品（不参与节日生命周期）
    if any(str(record.get(f) or "") == "全年" for f in
           ("launch_stage", "growth_stage", "hot_period", "mature_stage", "decline_stage")):
        record["notes"] = ((record["notes"] or "") + "；全年销售（长期产品，不参与节日生命周期）").strip("；")

    # 2) 节日/主题时间：描述性文本 → AI 提取一个或多个时间段；
    #    festival_date 保留主日期（首个时间段起点，向后兼容），festival_end 沿用原结束列
    raw_date_text = str(row[2]).strip() if len(row) > 2 and row[2] else ""
    if raw_date_text and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date_text) and use_ai:
        periods = await ai_extract_periods(raw_date_text, festival, "节日/主题时间", year)
        if periods:
            record["festival_periods"] = json.dumps(periods, ensure_ascii=False)
            if record["festival_date"] is None:
                record["festival_date"] = datetime.strptime(periods[0]["start"], "%Y-%m-%d")
        else:
            # 规则兜底：文本（含结束时间列）中出现具体日期（如 2027年2月9日）→ 单日时间段
            fallback_dates = _extract_dates_from_text(raw_date_text, year)
            end_text = str(row[3]).strip() if len(row) > 3 and row[3] else ""
            fallback_dates += _extract_dates_from_text(end_text, year)
            if fallback_dates:
                single = {
                    "start": fallback_dates[0].isoformat(),
                    "end": fallback_dates[0].isoformat(),
                    "label": festival,
                }
                record["festival_periods"] = json.dumps([single], ensure_ascii=False)
                record["festival_date"] = datetime.combine(fallback_dates[0], datetime.min.time())
    return record


def parse_hot_period(period_str) -> tuple:
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


async def import_festival_rows(session: AsyncSession, rows: list, use_ai: bool = True) -> dict:
    """导入：按节日覆盖更新 festival_calendar，返回统计"""
    year = date.today().year
    records = []
    ai_used = 0
    for row in rows:
        rec = await parse_row_to_record(row, year, use_ai)
        if rec:
            records.append(rec)
            if use_ai and rec.get("notes") and "AI" in str(rec.get("notes") or ""):
                ai_used += 1
    if not records:
        return {"imported": 0, "ai_used": 0, "festivals": []}
    festivals = {r["festival"] for r in records}
    await session.execute(delete(FestivalCalendar).where(FestivalCalendar.festival.in_(festivals)))
    for r in records:
        session.add(FestivalCalendar(**r))
    await session.commit()
    return {"imported": len(records), "ai_used": ai_used, "festivals": sorted(festivals)}
