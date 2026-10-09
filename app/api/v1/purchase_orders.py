# -*- coding: utf-8 -*-
"""采购单产品明细 网页API 路由

提供手动触发 orderListsV2 采购单产品明细同步的接口（后台执行，结果写入同步日志）。
"""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.tasks.sync_tasks import sync_purchase_order_items

router = APIRouter()


@router.post("/sync")
async def sync_purchase_order_items_api(
    mode: str = Query("full", description="full=全量；recent30=近30天"),
):
    """手动触发采购单产品明细同步（后台执行，结果写入同步日志）"""
    if mode not in ("full", "recent30"):
        raise HTTPException(status_code=400, detail="mode 仅支持 full / recent30")
    asyncio.create_task(sync_purchase_order_items(mode=mode))
    return {"message": f"采购单产品明细同步已触发（mode={mode}）", "mode": mode}
