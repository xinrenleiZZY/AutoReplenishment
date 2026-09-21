# -*- coding: utf-8 -*-
"""ASIN 语义分类服务

调用 DeepSeek AI 分析亚马逊标题（products.listing_title），判定属于「装饰品」还是「非装饰品」，
结果以 ASIN 为主键落库（semantic_classifications），供缓存天数判定（装饰品 14 天 / 非装饰品 3 天）使用。
"""

import asyncio
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import Product
from app.models.semantic_classification import SemanticClassification
from app.services.ai_eval import ai_enabled, chat_completion, _parse_json_text

logger = logging.getLogger(__name__)

# AI 提示词（固定输入/输出 JSON 格式）
SYSTEM_PROMPT = (
    "帮我识别以下标题属于装饰品还是非装饰品："
    "semantic_classification可填范围：装饰品/非装饰品。"
    '固定输入格式{"asin":"XXX","listing_title":"XXX"}，'
    '固定输出格式{"asin":"XXX","listing_title":"XXX","semantic_classification":"装饰品/非装饰品"}。'
    "只返回标准 JSON，不要输出任何其他内容。"
)

VALID_CLASSES = ("装饰品", "非装饰品")

# 缓存天数判定：装饰品=14，非装饰品=3
DECORATION_BUFFER_DAYS = 14
NON_DECORATION_BUFFER_DAYS = 3


def buffer_days(semantic_classification: str | None) -> int:
    """缓存天数判定：装饰品=14，非装饰品=3"""
    return DECORATION_BUFFER_DAYS if (semantic_classification or "").strip() == "装饰品" else NON_DECORATION_BUFFER_DAYS


async def get_semantic_classification(session: AsyncSession, asin: str) -> str | None:
    """读取 ASIN 的语义分类（无记录返回 None）"""
    if not asin:
        return None
    row = await session.get(SemanticClassification, asin)
    return row.semantic_classification if row else None


async def classify_one(asin: str, listing_title: str) -> str | None:
    """调用 AI 对单个亚马逊标题分类，返回「装饰品」/「非装饰品」，非法或失败返回 None"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"asin": asin, "listing_title": listing_title}, ensure_ascii=False
            ),
        },
    ]
    # 推理模型的 reasoning_tokens 会吃光输出预算导致正文为空，给足预算（API 上限 393216）
    text = await chat_completion(messages, temperature=0.0, max_tokens=393216, json_mode=True)
    data = _parse_json_text(text)
    value = (data.get("semantic_classification") or "").strip()
    if value not in VALID_CLASSES:
        logger.warning("ASIN %s 语义分类返回值非法: %s", asin, value)
        return None
    return value


async def refresh_semantic_classifications(
    session: AsyncSession, write: bool = True, limit: int | None = None,
    concurrency: int = 8,
) -> dict:
    """刷新启用产品的语义分类（亚马逊标题未变化的已有记录跳过）。返回统计。"""
    if not ai_enabled():
        logger.info("语义分类刷新跳过：AI 未启用")
        return {"total": 0, "classified": 0, "unchanged": 0, "failed": 0, "skipped": "ai_disabled"}

    products = (await session.execute(
        select(Product).where(Product.status == True)  # noqa: E712
    )).scalars().all()
    if limit:
        products = products[:limit]

    existing = {
        r.asin: r
        for r in (await session.execute(select(SemanticClassification))).scalars().all()
    }

    pending: list[tuple[str, str]] = []
    unchanged = 0
    for p in products:
        title = p.listing_title or ""
        row = existing.get(p.asin)
        if row and row.listing_title == title and row.semantic_classification in VALID_CLASSES:
            unchanged += 1
        else:
            pending.append((p.asin, title))

    # 并发调用 AI（classify_one 不触碰 session，写库在主流程串行完成）
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _worker(asin: str, title: str) -> tuple[str, str, str | None]:
        async with sem:
            try:
                return asin, title, await classify_one(asin, title)
            except Exception as e:  # noqa: BLE001
                logger.error("ASIN %s 语义分类失败: %s", asin, e)
                return asin, title, None

    results = await asyncio.gather(*(_worker(a, t) for a, t in pending))

    classified = failed = 0
    for asin, title, value in results:
        if value is None:
            failed += 1
            continue
        if write:
            row = existing.get(asin)
            if row:
                row.listing_title = title
                row.semantic_classification = value
            else:
                session.add(SemanticClassification(
                    asin=asin, listing_title=title, semantic_classification=value,
                ))
        classified += 1

    if write:
        await session.commit()
    logger.info("语义分类刷新完成: total=%s classified=%s unchanged=%s failed=%s",
                len(products), classified, unchanged, failed)
    return {
        "total": len(products),
        "classified": classified,
        "unchanged": unchanged,
        "failed": failed,
    }
