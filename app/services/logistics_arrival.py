"""物流追踪实时库服务（FBA在途库存到货时间）

用途：
  - 查询 FBA 在途库存的真实到货时间（arrival_date = 预计到港 + 14天）
  - 供库存分析与建议采购数量计算使用：可售库存缺口天数/缺口数量依据该到货时间

设计原则：
  - 调用物流实时库接口（http://192.168.0.193:7650）
  - 按天缓存全量 {ASIN: 最早到货时间}，避免每个 ASIN 重复请求
  - 失败/未配置时返回空映射，调用方回退估算到货时间，不影响主流程
"""

import logging
from datetime import date

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 按天缓存：{ASIN: 最早到货时间(YYYY-MM-DD)}
_cache: dict[str, str] | None = None
_cache_date: date | None = None


async def _fetch_arrival_map() -> dict[str, str]:
    """一次拉取全部 ASIN 的最早到货时间（earliest_only + upcoming_only）"""
    if not settings.LOGISTICS_API_URL:
        return {}
    url = f"{settings.LOGISTICS_API_URL.rstrip('/')}/api/v1/arrival/export"
    params = {"earliest_only": "true", "upcoming_only": "true"}
    headers = {"Content-Type": "application/json"}
    if settings.LOGISTICS_API_TOKEN:
        headers["X-API-Token"] = settings.LOGISTICS_API_TOKEN
    async with httpx.AsyncClient(timeout=settings.LOGISTICS_API_TIMEOUT) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"物流到货接口返回错误: {data.get('message')}，code={data.get('code')}")
    items = (data.get("data") or {}).get("items") or []
    mapping: dict[str, str] = {}
    for it in items:
        asin = str(it.get("asin") or "").strip().upper()
        time = str(it.get("time") or "").strip()
        if asin and time and (asin not in mapping or time < mapping[asin]):
            mapping[asin] = time
    return mapping


async def get_arrival_map(force: bool = False) -> dict[str, str]:
    """获取 {ASIN: 最早到货时间} 映射（按天缓存）

    force=True 强制刷新（不常用）；接口失败时返回上次缓存或空映射。
    成功响应（含空结果）同样按天缓存，避免每个 ASIN 重复请求。
    """
    global _cache, _cache_date
    today = date.today()
    if not force and _cache is not None and _cache_date == today:
        return _cache
    try:
        mapping = await _fetch_arrival_map()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"物流到货接口请求失败，回退缓存: {e}")
        return _cache or {}
    _cache, _cache_date = mapping, today
    return mapping


async def get_inbound_arrival_date(asin: str) -> str | None:
    """获取指定 ASIN 的最早 FBA在途到货时间（YYYY-MM-DD）；无记录/无配置返回 None"""
    if not settings.LOGISTICS_API_URL:
        return None
    asin = (asin or "").strip().upper()
    if not asin:
        return None
    mapping = await get_arrival_map()
    return mapping.get(asin)
