# -*- coding: utf-8 -*-
"""新老品判定辅助：品名特例关键词（老品 ASIN 复用场景）"""

from datetime import date

# 老品判定特例标签默认值（可在参数表 product_type_old_tags 维护）
OLD_TAGS_DEFAULT = "19年前,18年前"


def is_new_by_name_keywords(product_name, keywords: str | None) -> bool:
    """品名命中特例关键词（如 26版、27版）即视为新品"""
    if not product_name or not keywords:
        return False
    for kw in str(keywords).split(","):
        kw = kw.strip()
        if kw and kw in str(product_name):
            return True
    return False


def is_old_by_tags(tags, old_tags: str | None = None) -> bool:
    """listing 标签命中「老品判定特例标签」列表即视为老品（如 19年前、18年前）"""
    old_set = {t.strip() for t in str(old_tags if old_tags is not None else OLD_TAGS_DEFAULT).split(",") if t.strip()}
    if not old_set:
        return False
    from app.services.tag_festival import parse_tags

    return any(t in old_set for t in parse_tags(tags))


def is_new_product_basic(product, keywords: str = "26版,27版", old_tags: str | None = None) -> bool:
    """同步版新老品判定：品名特例关键词 → 老品特例标签 → 上架日期 → product_stage 兜底"""
    if is_new_by_name_keywords(getattr(product, "product_name", None), keywords):
        return True
    if is_old_by_tags(getattr(product, "tags", None), old_tags):
        return False
    list_date = getattr(product, "list_date", None)
    if list_date:
        return (date.today() - list_date).days <= 365
    stage = (getattr(product, "product_stage", "") or "").strip().lower()
    if stage in ("新品", "new"):
        return True
    if stage in ("老品", "old"):
        return False
    return False
