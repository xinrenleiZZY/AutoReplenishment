# -*- coding: utf-8 -*-
"""S/A 级产品包装规格补全（重量/尺寸）

匹配顺序（领星 product/lists 数据，box_qty_raw.jsonl）：
  1. SKU 精确匹配（ASIN 的 msku）
  2. SKU 归一化匹配
  3. 品名模糊匹配（difflib ≥ 0.70，用 listing 英文名/品名对照 ERP 品名）
  4. 找不到 → 保持为空（不写库）

用法: python scripts/backfill_package_specs.py [--levels S,A] [--dry-run]
"""
import argparse
import asyncio
import json
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select

from app.database import async_session_factory
from app.models.product import Product


def _f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _norm(s):
    s = (s or "").lower().strip()
    s = re.sub(r"^yps[\s_-]*", "", s)
    s = re.sub(r"^sku", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]", "", s)
    s = re.sub(r"\d+$", "", s)
    return s


def load_erp(raw_path) -> list:
    items = []
    with open(raw_path, encoding="utf-8") as f:
        for line in f:
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    return items


def item_specs(item) -> dict:
    l, w, h = _f(item.get("cg_package_length")), _f(item.get("cg_package_width")), _f(item.get("cg_package_height"))
    g = _f(item.get("cg_product_gross_weight"))
    return {
        "length_cm": l, "width_cm": w, "height_cm": h,
        "weight_kg": round(g / 1000.0, 4) if g > 0 else 0.0,
    }


def has_specs(spec) -> bool:
    return spec["length_cm"] > 0 or spec["width_cm"] > 0 or spec["height_cm"] > 0 or spec["weight_kg"] > 0


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", type=str, default="S,A", help="要处理的等级，逗号分隔")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    levels = [x.strip() for x in args.levels.split(",") if x.strip()]

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    raw_path = os.path.join(base, "p_id", "box_qty_raw.jsonl")
    msku_path = os.path.join(base, "p_id", "msku_id_full.json")
    if not os.path.exists(raw_path) or not os.path.exists(msku_path):
        print(f"缺少数据文件: {raw_path} / {msku_path}")
        return

    erp = load_erp(raw_path)
    print(f"ERP 数据 {len(erp)} 条")

    m_items = json.load(open(msku_path, encoding="utf-8"))
    m_items = m_items if isinstance(m_items, list) else list(m_items.values())
    asin_mskus: dict[str, set] = {}
    asin_names: dict[str, list] = {}
    for it in m_items:
        if not isinstance(it, dict):
            continue
        asin = (it.get("asin") or "").strip()
        if not asin:
            continue
        sku = (it.get("msku") or "").strip()
        if sku:
            asin_mskus.setdefault(asin, set()).add(sku)
        for k in ("local_name", "item_name", "product_name"):
            v = (it.get(k) or "").strip()
            if v:
                asin_names.setdefault(asin, []).append(v)

    # ERP 按归一化 sku / 原始 sku 索引
    by_norm_sku: dict[str, dict] = {}
    by_raw_sku: dict[str, dict] = {}
    by_name: list[tuple[str, dict]] = []
    for it in erp:
        sku = (it.get("sku") or "").strip()
        if sku:
            by_raw_sku.setdefault(sku, it)
            by_norm_sku.setdefault(_norm(sku), it)
        pn = (it.get("product_name") or "").strip()
        if pn:
            by_name.append((pn, it))

    async with async_session_factory() as session:
        rows = (await session.execute(
            select(Product).where(
                Product.status == True,  # noqa: E712
                Product.product_level.in_(levels),
            )
        )).scalars().all()
        matched_exact = matched_fuzzy = skipped = 0
        changed = 0
        examples = []
        for p in rows:
            spec = None
            if _f(p.weight_kg) > 0 and _f(p.length_cm) > 0 and _f(p.width_cm) > 0 and _f(p.height_cm) > 0:
                skipped += 1
                continue
            # 1/2. SKU 匹配
            for msku in asin_mskus.get(p.asin, []):
                it = by_raw_sku.get(msku) or by_norm_sku.get(_norm(msku))
                if it:
                    spec = item_specs(it)
                    if has_specs(spec):
                        matched_exact += 1
                        break
            # 3. 品名模糊匹配
            if spec is None or not has_specs(spec):
                names = asin_names.get(p.asin, []) + [p.product_name or ""]
                best_ratio, best_it = 0.0, None
                for pn, it in by_name:
                    if not has_specs(item_specs(it)):
                        continue
                    ratio = max(SequenceMatcher(None, _norm(n), _norm(pn)).ratio() for n in names)
                    if ratio > best_ratio:
                        best_ratio, best_it = ratio, it
                if best_ratio >= 0.70 and best_it is not None:
                    spec = item_specs(best_it)
                    matched_fuzzy += 1
            if spec is None or not has_specs(spec):
                continue  # 找不到 → 保持为空
            vals = {k: v for k, v in spec.items() if v and v > 0}
            if not vals:
                continue
            if len(examples) < 20:
                examples.append((p.asin, p.product_name[:24], vals, matched_fuzzy > 0))
            if not args.dry_run:
                for k, v in vals.items():
                    setattr(p, k, v)
            changed += 1
        if not args.dry_run:
            await session.commit()
        print(f"dry_run={args.dry_run} 目标等级={levels} 产品={len(rows)} 已完成={skipped} "
              f"SKU匹配={matched_exact} 品名模糊={matched_fuzzy} 本次补全={changed}")
        for asin, name, vals, fuzzy in examples:
            print(f"  {asin} {name} {'[模糊]' if fuzzy else ''} {vals}")


if __name__ == "__main__":
    asyncio.run(main())
