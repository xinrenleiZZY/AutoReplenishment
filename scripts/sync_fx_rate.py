# -*- coding: utf-8 -*-
"""实时汇率同步：Google Finance USD/CNY（失败回退 open.er-api）

写入 config_params：usd_cny_rate / cost_exchange_rate / fx_updated_at
用法: python scripts/sync_fx_rate.py
"""
import asyncio
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.database import async_session_factory
from app.services.config_service import set_param

GOOGLE_URL = "https://www.google.com/finance/quote/USD-CNY"
ERAPI_URL = "https://open.er-api.com/v6/latest/USD"


async def fetch_rate() -> float | None:
    # 优先 Google Finance
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            r = await client.get(GOOGLE_URL, headers={"User-Agent": "Mozilla/5.0"})
            m = re.search(r'data-last-price="([0-9.]+)"', r.text) or re.search(r"<span[^>]*>([0-9]+\.[0-9]{4})</span>", r.text)
            if m:
                return round(float(m.group(1)), 4)
    except Exception as e:  # noqa: BLE001
        print("Google Finance 获取失败:", e)
    # 兜底 open.er-api
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            data = (await client.get(ERAPI_URL)).json()
            rate = (data.get("rates") or {}).get("CNY")
            if rate:
                return round(float(rate), 4)
    except Exception as e:  # noqa: BLE001
        print("open.er-api 获取失败:", e)
    return None


async def main():
    rate = await fetch_rate()
    if not rate:
        print("未获取到汇率，跳过更新")
        return
    async with async_session_factory() as session:
        await set_param(session, "usd_cny_rate", str(rate))
        await set_param(session, "cost_exchange_rate", str(rate))
        await set_param(session, "fx_updated_at", datetime.now().strftime("%Y-%m-%d %H:%M"))
        await session.commit()
    print(f"汇率已更新: USD/CNY = {rate}")


if __name__ == "__main__":
    asyncio.run(main())
