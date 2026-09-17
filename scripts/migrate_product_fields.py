"""产品表新增字段迁移：按 Product 模型补齐缺失列（幂等，可重复执行）

用法: python -m scripts.migrate_product_fields
"""

import asyncio

from sqlalchemy import text

from app.database import engine
from app.models.product import Product


async def migrate() -> list[str]:
    added: list[str] = []
    async with engine.begin() as conn:
        rows = await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'products'"
        ))
        existing = {r[0] for r in rows.fetchall()}
        for column in Product.__table__.columns:
            name = column.name
            if name in existing:
                continue
            col_type = column.type.compile(engine.dialect)
            ddl = f"ALTER TABLE products ADD COLUMN {name} {col_type}"
            if column.nullable is False and column.default is None:
                ddl += " NOT NULL"
            await conn.execute(text(ddl))
            added.append(f"{name} {col_type}")
    return added


async def main():
    added = await migrate()
    if added:
        print(f"已新增 {len(added)} 列:")
        for a in added:
            print("  +", a)
    else:
        print("无需迁移，products 表已包含全部字段")


if __name__ == "__main__":
    asyncio.run(main())
