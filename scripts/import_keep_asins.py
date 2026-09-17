"""导入「需要加回去的 ASIN」到保留列表（listing_keep_asins），并从排除列表移除

优先级：保留列表 > 排除列表。执行流程：
  1. 解析文件中的 ASIN（支持 txt/csv/xlsx，自动识别 B0 开头的 10 位 ASIN）
  2. 从 listing_exclude_asins 中移除这些 ASIN
  3. 合并写入 listing_keep_asins（去重）
  4. 恢复库中这些产品的在售状态（status=True，status_text=在售）
  5. 可选触发产品重新获取数据（sync_products），让领星数据重新导入

用法:
    python -m scripts.import_keep_asins --file "docs/需要加回去的ASIN.xlsx" [--dry-run] [--no-sync]
"""

import argparse
import asyncio
import io
import logging
import re

from sqlalchemy import select, update

from app.database import async_session_factory
from app.models.product import Product
from app.services import config_service

logger = logging.getLogger(__name__)


def parse_asins_file(filename: str, content: bytes) -> list:
    """从 txt/csv/xlsx 中解析 ASIN 列表（去重、取10位ASIN）"""
    name = (filename or "").lower()
    raw = []
    if name.endswith((".xlsx", ".xls")):
        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if cell is not None:
                    raw.append(str(cell))
    else:
        raw = content.decode("utf-8", errors="ignore").splitlines()

    result = []
    for token in raw:
        token = (token or "").strip().upper()
        if not token:
            continue
        m = re.search(r"\bB0[A-Z0-9]{8}\b", token)
        if m:
            token = m.group(0)
        if re.fullmatch(r"[A-Z0-9]{10}", token) and token not in result:
            result.append(token)
    return result


def _split(raw: str) -> set:
    return {a for a in re.split(r"[\s,;，；]+", str(raw or "")) if a}


async def run(file: str, dry_run: bool, sync: bool) -> dict:
    with open(file, "rb") as f:
        content = f.read()
    asins = parse_asins_file(file, content)
    if not asins:
        raise ValueError("未识别到有效 ASIN")

    stats = {
        "total": len(asins),
        "in_exclude_list": 0,
        "in_db": 0,
        "new_keep": 0,
        "exclude_after": None,
        "keep_after": None,
    }

    async with async_session_factory() as session:
        keep_raw = await config_service.get_param(session, "listing_keep_asins") or ""
        exclude_raw = await config_service.get_param(session, "listing_exclude_asins") or ""
        keep_old = _split(keep_raw)
        exclude_old = _split(exclude_raw)

        to_keep = set(asins)
        stats["in_exclude_list"] = len(to_keep & exclude_old)
        stats["new_keep"] = len(to_keep - keep_old)
        new_exclude = sorted(exclude_old - to_keep)
        new_keep = sorted(keep_old | to_keep)
        stats["exclude_after"] = len(new_exclude)
        stats["keep_after"] = len(new_keep)

        db_rows = await session.execute(
            select(Product.asin).where(Product.asin.in_(sorted(to_keep)))
        )
        stats["in_db"] = len(db_rows.all())

        if dry_run:
            return stats

        await config_service.set_param(session, "listing_exclude_asins", ",".join(new_exclude))
        await config_service.set_param(session, "listing_keep_asins", ",".join(new_keep))

        result = await session.execute(
            update(Product)
            .where(Product.asin.in_(sorted(to_keep)))
            .values(status=True, status_text="在售")
        )
        stats["restored"] = result.rowcount or 0
        await session.commit()

    if sync and not dry_run:
        from app.tasks.sync_tasks import sync_products

        stats["sync_submitted"] = True
        asyncio.create_task(sync_products())
    else:
        stats["sync_submitted"] = False
    return stats


def main():
    parser = argparse.ArgumentParser(description="加回保留列表并重新获取数据")
    parser.add_argument("--file", default=r"docs/需要加回去的ASIN.xlsx", help="ASIN 文件（txt/csv/xlsx）")
    parser.add_argument("--dry-run", action="store_true", help="只查看影响，不写库")
    parser.add_argument("--no-sync", action="store_true", help="不触发产品重新获取")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    stats = asyncio.run(run(args.file, args.dry_run, not args.no_sync))
    mode = "DRY-RUN（未写库）" if args.dry_run else "已执行"
    print(f"[{mode}] 文件 ASIN {stats['total']} 个 | 排除列表命中 {stats['in_exclude_list']} | 库中存在 {stats['in_db']} | 新增保留 {stats['new_keep']}")
    print(f"  排除列表: {stats['exclude_after']} 条 | 保留列表: {stats['keep_after']} 条"
          + (f" | 恢复在售 {stats.get('restored', 0)}" if not args.dry_run else ""))
    if stats.get("sync_submitted"):
        print("  已提交产品重新获取（异步执行）")


if __name__ == "__main__":
    main()
