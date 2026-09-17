# -*- coding: utf-8 -*-
"""把 tag_festival.py 内置默认标签映射同步进数据库表 tag_festival_map

- 首次建库种子（表不存在会自动创建）；
- 日常以数据库表为准，新增/修正映射直接改表或在此维护默认种子后重跑；
- --overwrite 时强制用默认种子覆盖同名标签的节日映射。

用法:
  python scripts/sync_tag_festival_map.py            # 只新增缺失映射
  python scripts/sync_tag_festival_map.py --overwrite # 用默认种子覆盖同名映射
"""
import argparse
import asyncio
import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory, Base, engine
from app.models.tag_festival_map import TagFestivalMap
from app.services.tag_festival import DEFAULT_TAG_FESTIVAL, DEFAULT_SEASON_TAGS


async def main(overwrite: bool):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as s:
        existing = {r.tag: r for r in (await s.execute(select(TagFestivalMap))).scalars().all()}
        added = updated = 0
        for category, mapping in (("festival", DEFAULT_TAG_FESTIVAL), ("season", DEFAULT_SEASON_TAGS)):
            for tag, festival in mapping.items():
                row = existing.get(tag)
                if row is None:
                    s.add(TagFestivalMap(tag=tag, festival=festival, category=category))
                    added += 1
                elif overwrite and (row.festival != festival or row.category != category):
                    row.festival = festival
                    row.category = category
                    updated += 1
        await s.commit()
        total = len((await s.execute(select(TagFestivalMap))).scalars().all())
        print(f"同步完成: 新增 {added} / 覆盖 {updated} / 表内共 {total}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true", help="用默认种子覆盖同名标签映射")
    args = parser.parse_args()
    asyncio.run(main(args.overwrite))
