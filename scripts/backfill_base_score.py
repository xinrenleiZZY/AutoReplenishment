# -*- coding: utf-8 -*-
"""回填 calculation_results.base_score（原始公式分）

逻辑：
- score_detail 含六维评分（新品在 "六维评分" 子对象，老品在顶层 shortage_score/trend_score/
  profit_score/life_score/urgency/transport_score），用 值×权重 反推原始公式分并落库；
- 无六维数据的行 base_score 回退为 purchase_score（视为未调整）。

用法: python scripts/backfill_base_score.py
"""
import asyncio
import json
import sys

import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, update
from sqlalchemy import text

from app.database import async_session_factory
from app.models.calculation import CalculationResult

DIM_KEYS = ("shortage_score", "trend_score", "profit_score", "life_score", "urgency", "transport_score")


def compute_base(score_detail) -> float | None:
    """从六维评分反推原始公式分；无数据返回 None"""
    if not score_detail or not isinstance(score_detail, dict):
        return None
    dims = score_detail.get("六维评分")
    if not isinstance(dims, dict):
        dims = score_detail
    total = 0.0
    found = 0
    for key in DIM_KEYS:
        item = dims.get(key)
        if not isinstance(item, dict):
            continue
        try:
            value = float(item.get("value"))
            weight = float(item.get("weight"))
        except (TypeError, ValueError):
            continue
        total += value * weight
        found += 1
    if found == 0:
        return None
    return round(total, 2)


async def main():
    async with async_session_factory() as s:
        rows = (await s.execute(select(CalculationResult.id, CalculationResult.purchase_score,
                                       CalculationResult.score_detail))).all()
        print(f"待回填: {len(rows)} 行")
        updates = []
        for rid, purchase_score, score_detail_raw in rows:
            try:
                sd = json.loads(score_detail_raw or "{}")
            except (json.JSONDecodeError, TypeError):
                sd = {}
            base = compute_base(sd)
            if base is None:
                base = round(float(purchase_score), 2) if purchase_score is not None else None
            updates.append({"id": rid, "base": base})
        # 分批提交
        for i in range(0, len(updates), 500):
            chunk = updates[i:i + 500]
            for u in chunk:
                await s.execute(
                    text("UPDATE calculation_results SET base_score = :base WHERE id = :id"),
                    {"base": u["base"], "id": u["id"]},
                )
            await s.commit()
            print(f"已回填 {i + len(chunk)}/{len(updates)}")
        print("完成")


if __name__ == "__main__":
    asyncio.run(main())
