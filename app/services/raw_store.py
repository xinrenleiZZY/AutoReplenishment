# -*- coding: utf-8 -*-
"""API 原始数据归档：所有领星/SIF 请求的完整原始响应统一入库 api_raw_responses

用法：
  1. 任意抓取代码在拿到响应后调用 collect_raw(...)（同步、线程安全、几乎零开销）；
  2. 每个同步任务/脚本末尾调用 await flush_raw() 批量写入数据库。
  归档失败绝不影响主流程。
"""

import json
import threading
from datetime import date

from app.database import async_session_factory
from app.models.api_raw import ApiRawResponse

_lock = threading.Lock()
_queue: list[dict] = []


def _to_jsonable(response):
    if response is None:
        return None
    if isinstance(response, (dict, list)):
        return response
    if isinstance(response, str):
        try:
            return json.loads(response)
        except (ValueError, TypeError):
            return {"raw_text": response[:100000]}
    return {"raw_text": str(response)[:100000]}


def collect_raw(source, response, asin=None, url=None, method=None, params=None,
                status_code=200, error=None, fetch_date=None):
    """同步收集一条原始响应（线程安全），不阻塞调用方"""
    try:
        item = {
            "source": str(source)[:80],
            "asin": asin,
            "request_url": url,
            "request_method": method,
            "request_params": params,
            "response_raw": _to_jsonable(response),
            "status_code": status_code,
            "error": str(error)[:2000] if error else None,
            "fetch_date": fetch_date or date.today(),
        }
        with _lock:
            _queue.append(item)
    except Exception:  # noqa: BLE001
        pass


async def flush_raw(batch_size: int = 200) -> int:
    """把收集队列中的原始响应批量写入数据库；返回写入条数（队列为空返回 0）"""
    with _lock:
        items = _queue[:batch_size]
        del _queue[:batch_size]
    if not items:
        return 0
    try:
        async with async_session_factory() as s:
            for it in items:
                s.add(ApiRawResponse(**it))
            await s.commit()
        return len(items)
    except Exception:  # noqa: BLE001
        # 写入失败放回队列，下次再刷，避免原始数据丢失
        with _lock:
            _queue[0:0] = items
        return 0


async def save_raw(source, response, asin=None, url=None, method=None, params=None,
                   status_code=200, error=None, fetch_date=None) -> bool:
    """异步直接写入一条原始响应（异步调用方使用）"""
    try:
        async with async_session_factory() as s:
            s.add(ApiRawResponse(
                source=str(source)[:80],
                asin=asin,
                request_url=url,
                request_method=method,
                request_params=params,
                response_raw=_to_jsonable(response),
                status_code=status_code,
                error=str(error)[:2000] if error else None,
                fetch_date=fetch_date or date.today(),
            ))
            await s.commit()
        return True
    except Exception:  # noqa: BLE001
        return False
