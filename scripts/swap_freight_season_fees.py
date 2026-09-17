# -*- coding: utf-8 -*-
"""修正运费淡旺季数值：业务规则 淡季=便宜、旺季=贵（旺季=8-12月）。

此前 DB 中存的是 淡季>旺季（数值反过来），此脚本将已存的 6 个运费参数对调：
  海运 sea_slow 17→15 / sea_peak 15→17
  空派 air_slow 70→60 / air_peak 60→70
  快递 express_slow 80→70 / express_peak 70→80
仅处理已存在的行；未配置的行由 config.py / TRANSPORT_MODES 默认值兜底。
用法: python scripts/swap_freight_season_fees.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from sqlalchemy import select

from app.database import async_session_factory
from app.models.config import ConfigParam

# (slow_key, peak_key, 修正后 slow, 修正后 peak)
TARGETS = [
    ("sea_slow_fee", "sea_peak_fee", 15, 17),
    ("air_slow_fee", "air_peak_fee", 60, 70),
    ("express_slow_fee", "express_peak_fee", 70, 80),
]


async def main():
    changed = []
    async with async_session_factory() as session:
        for slow_key, peak_key, new_slow, new_peak in TARGETS:
            rows = (await session.execute(
                select(ConfigParam).where(ConfigParam.param_key.in_([slow_key, peak_key]))
            )).scalars().all()
            by_key = {r.param_key: r for r in rows}
            for key, val in ((slow_key, new_slow), (peak_key, new_peak)):
                row = by_key.get(key)
                if row is not None:
                    if row.param_value != str(val):
                        old = row.param_value
                        row.param_value = str(val)
                        changed.append(f"{key}: {old} → {val}")
                else:
                    # 未配置（走默认值）则不写入
                    pass
        await session.commit()

    if changed:
        print("已对调运费参数：")
        for line in changed:
            print("  " + line)
    else:
        print("DB 中未发现已保存的运费参数（走代码默认值），无需对调；若需落库可手动保存参数页。")


if __name__ == "__main__":
    asyncio.run(main())
