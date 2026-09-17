# -*- coding: utf-8 -*-
"""产品负责人修正：优先使用运营人员表中的姓名（模糊匹配），没有匹配则保留原值

用法:
  python -m scripts.fix_product_operators --dry-run   # 预览将改动的产品
  python -m scripts.fix_product_operators             # 实际写库
"""
import asyncio
import sys

from sqlalchemy import select

from app.database import async_session_factory
from app.models.operator import Operator
from app.models.product import Product
from app.services.operator_sync import clean_operator_name


async def main(dry_run: bool):
    async with async_session_factory() as session:
        # 运营人员表 → 清洗名 → 规范名（规范名即清洗后的干净姓名）
        ops = (await session.execute(select(Operator))).scalars().all()
        canonical: dict[str, str] = {}
        for op in ops:
            cleaned = clean_operator_name(op.name)
            if not cleaned:
                continue
            canonical.setdefault(cleaned, cleaned)
        keys = set(canonical)

        products = (await session.execute(
            select(Product).where(Product.status == True)  # noqa: E712
        )).scalars().all()

        updated = 0
        kept = 0
        examples = []
        for p in products:
            raw = (p.operator or "").strip()
            if not raw:
                continue
            parts = [part.strip() for part in raw.split(",") if part.strip()]
            out = []
            for part in parts:
                name = clean_operator_name(part)
                if not name:
                    continue
                hit = canonical.get(name)
                if hit is None:
                    # 模糊匹配：互相包含（如 张洁莹 与 张洁莹1.3% 清洗后相等已在上面命中）
                    for k in keys:
                        if name in k or k in name:
                            hit = canonical[k]
                            break
                if hit and hit not in out:
                    out.append(hit)
            new_val = ",".join(out)
            if not new_val:
                kept += 1  # 无匹配：保留原值
                continue
            if new_val != raw:
                if len(examples) < 25:
                    examples.append((p.asin, raw, new_val))
                if not dry_run:
                    p.operator = new_val
                updated += 1

        if not dry_run:
            await session.commit()

        print(f"dry_run={dry_run} 在售产品={len(products)} 有负责人={len(products) - sum(1 for p in products if not (p.operator or '').strip())} "
              f"更新={updated} 无匹配保留={kept} 运营人员候选={len(keys)}")
        for asin, old, new in examples:
            print(f"  {asin}: {old!r} -> {new!r}")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
