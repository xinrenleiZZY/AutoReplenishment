# -*- coding: utf-8 -*-
"""新老品判定辅助：品名特例关键词（老品 ASIN 复用场景）"""

from datetime import date


def is_new_by_name_keywords(product_name, keywords: str | None) -> bool:
    """品名命中特例关键词（如 26版、27版）即视为新品"""
    if not product_name or not keywords:
        return False
    for kw in str(keywords).split(","):
        kw = kw.strip()
        if kw and kw in str(product_name):
            return True
    return False


def is_new_product_basic(product, keywords: str = "26版,27版") -> bool:
    """同步版新老品判定：品名特例关键词 → 上架日期 → product_stage 兜底"""
    if is_new_by_name_keywords(getattr(product, "product_name", None), keywords):
        return True
    list_date = getattr(product, "list_date", None)
    if list_date:
        return (date.today() - list_date).days <= 365
    stage = (getattr(product, "product_stage", "") or "").strip().lower()
    if stage in ("新品", "new"):
        return True
    if stage in ("老品", "old"):
        return False
    return False
