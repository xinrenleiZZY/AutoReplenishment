# -*- coding: utf-8 -*-
"""原始接口响应保留策略（Phase 3 / B-07）

默认 **dry-run：只统计不删数据**；必须显式加 --yes 才会执行。

用法：
  python scripts/purge_raw_responses.py                                  # 只统计（含表体量）
  python scripts/purge_raw_responses.py --days 90 --archive --yes        # 先归档 _archive/*.jsonl.gz 再删除
  python scripts/purge_raw_responses.py --days 90 --no-archive --yes     # 直接删除（不归档）
"""
import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import raw_retention


async def main() -> int:
    parser = argparse.ArgumentParser(description="api_raw_responses 保留策略（默认 dry-run）")
    parser.add_argument("--days", type=int, default=raw_retention.DEFAULT_RETENTION_DAYS,
                        help="保留天数，早于 today-days 的行会被归档/删除")
    parser.add_argument("--archive", dest="archive", action="store_true", default=True,
                        help="先归档成 _archive/*.jsonl.gz 再删除（默认开启）")
    parser.add_argument("--no-archive", dest="archive", action="store_false",
                        help="不归档，直接删除")
    parser.add_argument("--archive-dir", default=raw_retention.DEFAULT_ARCHIVE_DIR)
    parser.add_argument("--yes", action="store_true", help="确认执行（不加则只统计）")
    args = parser.parse_args()

    before = await raw_retention.stats()
    print("治理前:", json.dumps(before, ensure_ascii=False))
    result = await raw_retention.archive_and_purge(
        days=args.days,
        archive_dir=(args.archive_dir if args.archive else None),
        dry_run=not args.yes,
    )
    print("结果:", json.dumps(result, ensure_ascii=False))
    if not args.yes:
        print("（dry-run：未改动数据；确认请加 --yes）")
    print("治理后:", json.dumps(await raw_retention.stats(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
