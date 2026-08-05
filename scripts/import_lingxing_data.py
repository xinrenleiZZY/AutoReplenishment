"""
领星产品数据导入脚本
从 p_id/msku_id_full.json 读取全量产品数据，写入 PostgreSQL products 表
"""

import asyncio
import json
import os
import sys
import re
import logging
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from app.models.product import Product
from sqlalchemy import select, update

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_category_text(category_text: str) -> tuple:
    """解析 category_text 如 '纸质印刷类\纸质套装类' → (一级分类, 二级分类)"""
    if not category_text:
        return (None, None)
    parts = category_text.split("\\")
    if len(parts) >= 2:
        return (parts[0].strip(), parts[1].strip())
    return (parts[0].strip(), None)


def parse_open_date(open_date_str) -> date:
    """解析 open_date '2025-05-08 17:55:20 PDT' → date"""
    if not open_date_str:
        return None
    try:
        # 格式如 '2025-05-08 17:55:20 PDT'
        match = re.match(r"(\d{4}-\d{2}-\d{2})", str(open_date_str))
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except Exception:
        pass
    return None


def parse_small_rank(small_rank) -> str:
    """解析 small_rank JSON → 存为文本"""
    if not small_rank:
        return None
    if isinstance(small_rank, (list, dict)):
        return json.dumps(small_rank, ensure_ascii=False)
    return str(small_rank)


async def import_products(json_path: str) -> int:
    """从 JSON 全量导入产品数据（安全更新：只更新基础字段，不覆盖等级/工期/箱规等维护字段）

    返回更新/插入的记录数。
    """
    logger.info(f"读取产品数据: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    logger.info(f"共 {len(items)} 条产品记录")

    session = async_session_factory()
    try:
        async with session:
            data_map = {}
            errors = 0
            for idx, item in enumerate(items):
                asin = item.get("asin", "").strip()
                if not asin:
                    continue

                try:
                    category, sub_category = parse_category_text(item.get("category_text", ""))

                    product_type_val = item.get("product_type")
                    product_type = None
                    if product_type_val == 2:
                        product_type = "节日产品"
                    elif product_type_val == 1:
                        product_type = "长期产品"

                    values = {
                        "product_name": (item.get("item_name") or "")[:2000],
                        "category": category,
                        "sub_category": sub_category,
                        "list_date": parse_open_date(item.get("open_date")),
                        "product_type": product_type,
                        "status": True,
                        "operator": item.get("principal_realname"),
                    }
                    values = {k: v for k, v in values.items() if v is not None}
                    data_map[asin] = values
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.warning(f"  跳过 ASIN={asin}: {e}")

            # 已存在的产品 → 更新基础字段；新产品 → 插入
            asins = list(data_map.keys())
            exist = await session.execute(select(Product.asin).where(Product.asin.in_(asins)))
            exist_set = set(exist.scalars().all())
            updated = 0
            inserted = 0
            for i, (asin, values) in enumerate(data_map.items()):
                if asin in exist_set:
                    await session.execute(update(Product).where(Product.asin == asin).values(**values))
                    updated += 1
                else:
                    session.add(Product(asin=asin, **values))
                    inserted += 1
                if (i + 1) % 500 == 0:
                    await session.flush()
                    logger.info(f"  已处理 {i + 1}/{len(asins)} 条...")
            await session.commit()
            logger.info(f"导入完成: 更新 {updated} 条, 新增 {inserted} 条, 失败 {errors} 条")
            return updated + inserted
    finally:
        await session.close()


async def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(base_dir, "p_id", "msku_id_full.json")

    if not os.path.exists(json_path):
        logger.error(f"文件不存在: {json_path}")
        return

    await import_products(json_path)


if __name__ == "__main__":
    asyncio.run(main())
