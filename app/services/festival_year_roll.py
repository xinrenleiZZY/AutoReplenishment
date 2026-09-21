"""节日日历年份自更新

festival_calendar 的「上架开售 / 节日时间 / 结束时间」三个日期字段按自然年滚动：
每年跨过 12 月 31 日 23:59，三字段年份全部 +1。

- 定时触发：scheduler 每年 12-31 23:59 执行 roll_festival_years(+1)
- 启动兜底：应用启动时 ensure_festival_year_current 比对基准年份，补齐漏跑
基准年份记录在配置参数 festival_year_base（= 当前数据所对齐的年份）。
"""

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.festival_calendar import FestivalCalendar

logger = logging.getLogger(__name__)

FESTIVAL_DATE_FIELDS = ("listing_start", "festival_date", "festival_end")
BASE_YEAR_PARAM = "festival_year_base"


def _shift_years(dt, years: int):
    """日期整体平移 years 年（2-29 落非闰年时取 2-28）"""
    if dt is None or not years:
        return dt
    try:
        return dt.replace(year=dt.year + years)
    except ValueError:
        return dt.replace(year=dt.year + years, day=28)


async def roll_festival_years(session: AsyncSession, years: int = 1) -> dict:
    """三个日期字段年份整体 +years"""
    rows = (await session.execute(select(FestivalCalendar))).scalars().all()
    fields = 0
    for row in rows:
        for name in FESTIVAL_DATE_FIELDS:
            value = getattr(row, name)
            if value is None:
                continue
            setattr(row, name, _shift_years(value, years))
            fields += 1
    await session.commit()
    logger.info("节日日历年份滚动完成：%s 行 / %s 个字段 / +%s 年", len(rows), fields, years)
    return {"rows": len(rows), "fields": fields, "years": years}


async def ensure_festival_year_current(session: AsyncSession) -> dict:
    """启动兜底：基准年份落后于当前年份时补齐滚动（定时任务漏跑场景）"""
    from app.services.config_service import get_param, set_param

    today_year = date.today().year
    base = int(await get_param(session, BASE_YEAR_PARAM) or 0)
    if base <= 0:
        await set_param(session, BASE_YEAR_PARAM, today_year)
        logger.info("节日日历年份基准初始化：%s", today_year)
        return {"base": today_year, "rolled": 0}
    if base >= today_year:
        return {"base": base, "rolled": 0}
    diff = today_year - base
    stats = await roll_festival_years(session, diff)
    await set_param(session, BASE_YEAR_PARAM, today_year)
    logger.info("节日日历年份补滚动：基准 %s → %s（+%s 年）", base, today_year, diff)
    return {"base": today_year, "rolled": diff, **stats}
