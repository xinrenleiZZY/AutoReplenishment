"""计算引擎共用小工具（C1 拆分批1 · Phase 3 / B-01）

这些函数原先散落在 app/tasks/calculation_tasks.py 中，被全流程复用；
单独成模块是为了避免 calc.* 与 calculation_tasks 的循环导入。搬运为逐字节剪切，未改逻辑。
"""

import json


def _safe_int(val, default=0):
    if val is None:
        return default
    return int(val)


def _safe_float(val, default=0.0):
    if val is None:
        return default
    return float(val)


def _to_float_safe(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _parse_step_json(s: str | None):
    """JSON字符串转对象，用于API返回展示"""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s


