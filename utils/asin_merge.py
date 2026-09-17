# -*- coding: utf-8 -*-
"""同 ASIN 多条记录合并工具（在售优先 + 同量保大 + 列级非0合并）

规则（用户 2026-08-24 定义）：
  1. 在售优先：已有在售行，新行停售/已删除 → 保留在售行（反向同理）；
  2. 同在售（或同停售）：以「近30天销量」大的一行为基础（同量保大）；
  3. 列级合并：基础行为空(0/None)的**数值字段**，若另一行有非 0 值 → 合并进来。

示例：
  elf hat     在售  可售75 预留8  在途0   30天53
  jingle bell 在售  可售0  预留0  在途521 30天0
  → 合并: 可售75 预留8 在途521 30天53（在途取自另一行，不丢数据）

仅合并数值字段，字符串/JSON 身份字段（msku/品名/分类等）保持基础行，避免串行。
"""


def _num(v) -> float:
    """数值化（None/'' → 0）"""
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _is_empty(v) -> bool:
    """是否视为空值（参与列级合并的补充条件）"""
    if v is None or v == "":
        return True
    if isinstance(v, (int, float)) and v == 0:
        return True
    return False


def _is_numeric(v) -> bool:
    """是否数值字段（int/float/可数值化字符串；bool 除外）"""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    if isinstance(v, str):
        try:
            float(v)
            return True
        except (ValueError, TypeError):
            return False
    return False


def merge_asin_records(existing: dict | None, incoming: dict | None,
                       vol_key: str = "thirty_volume",
                       active_key: str = "status") -> dict | None:
    """合并同一 ASIN 的两条记录 → 返回合并结果。

    Args:
        existing: 已合并的基线记录（可为 None）
        incoming: 新出现的记录
        vol_key: 销量比较字段（默认 thirty_volume，用于"同量保大"选基础行）
        active_key: 在售状态字段（默认 status，bool：True=在售）
    """
    if incoming is None:
        return existing
    if existing is None:
        return incoming

    prev_active = bool(existing.get(active_key))
    cur_active = bool(incoming.get(active_key))

    # 1) 在售优先：状态不同时保留在售行
    if prev_active and not cur_active:
        return existing
    if cur_active and not prev_active:
        return incoming

    # 2) 同状态：30天销量大者为基础（同量保大）
    if _num(existing.get(vol_key)) >= _num(incoming.get(vol_key)):
        base, other = existing, incoming
    else:
        base, other = incoming, existing

    # 3) 列级合并：基础行为空的数值字段，取另一行非 0 值
    merged = dict(base)
    for k, v in other.items():
        if _is_empty(merged.get(k)) and not _is_empty(v) and _is_numeric(v):
            merged[k] = v
    return merged
