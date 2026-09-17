# -*- coding: utf-8 -*-
"""
产品生命周期时间点表导入脚本
读取 docs/更新版-产品生命周期时间点2026.7.30.xlsx → 导入 festival_calendar 表

表格字段:
  节日/主题 | 亚马逊预计上架开卖时间 | 节日/主题时间 | 预设节日/主题结束时间
  | 启动期 | 增长期 | 热卖期 | 成熟期 | 下降期 | 备注

用法:
    python scripts/import_lifecycle_timing.py
"""

import asyncio
import os
import re
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from openpyxl import load_workbook

from app.database import async_session_factory, init_db
from app.models.festival_calendar import FestivalCalendar

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

XLSX_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "docs", "更新版-产品生命周期时间点2026.7.30.xlsx")


def _to_dt(value):
    """转 datetime，支持 datetime/date/字符串"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # Excel 序列号（自 1899-12-30 起天数）
        try:
            from datetime import timedelta
            return datetime(1899, 12, 30) + timedelta(days=int(value))
        except Exception:
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # 纯数字的 Excel 日期序列
        if s.isdigit():
            try:
                from datetime import timedelta
                return datetime(1899, 12, 30) + timedelta(days=int(s))
            except Exception:
                return None
        # 文本描述（如 "每年都不一样"）不解析
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def parse_hot_period(period_str) -> tuple:
    """解析热卖期字符串如 '1月上旬-中旬'、'11月-12月'，返回 (start, end) 或 None"""
    if not period_str:
        return None
    s = str(period_str).strip().replace(" ", "")
    if not s or s in ("-", "None"):
        return None
    # 匹配 '11-12月'、'10-1月'
    m = re.match(r"(\d{1,2})\s*[-–—]\s*(\d{1,2})月?", s)
    if m:
        return f"{int(m.group(1))}-{int(m.group(2))}月"
    # 单个 '6月'、'12月'
    m = re.match(r"(\d{1,2})月", s)
    if m:
        return f"{int(m.group(1))}月"
    return s


async def main():
    await init_db()
    wb = load_workbook(XLSX_PATH, data_only=True)
    ws = wb.active
    logger.info(f"读取文件: {XLSX_PATH}, 工作表: {ws.title}")

    session = async_session_factory()
    async with session:
        # 清空已有数据后重新导入
        await session.execute(FestivalCalendar.__table__.delete())

        count = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or not row[0] or not str(row[0]).strip():
                continue
            festival = str(row[0]).strip()

            record = FestivalCalendar(
                festival=festival,
                listing_start=_to_dt(row[1]),
                festival_date=_to_dt(row[2]),
                festival_end=_to_dt(row[3]),
                launch_stage=str(row[4]).strip() if row[4] else None,
                growth_stage=str(row[5]).strip() if row[5] else None,
                hot_period=str(row[6]).strip() if row[6] else None,
                mature_stage=str(row[7]).strip() if row[7] else None,
                decline_stage=str(row[8]).strip() if row[8] else None,
                notes=str(row[9]).strip() if len(row) > 9 and row[9] else None,
            )
            session.add(record)
            count += 1

        await session.commit()
        logger.info(f"导入完成: {count} 条节日/主题记录")

    # 验证
    async with session:
        from sqlalchemy import select
        result = await session.execute(select(FestivalCalendar).limit(5))
        for r in result.scalars():
            logger.info(f"  [{r.id}] {r.festival}: "
                        f"启动={r.launch_stage}, 增长={r.growth_stage}, "
                        f"热卖={r.hot_period}, 成熟={r.mature_stage}, 下降={r.decline_stage}")


if __name__ == "__main__":
    asyncio.run(main())
