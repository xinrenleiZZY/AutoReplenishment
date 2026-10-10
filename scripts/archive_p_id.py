# -*- coding: utf-8 -*-
"""p_id 抓取产物归档（Phase 3 / B-07）

把超过保留期的抓取文件**移动**到 _archive/p_id/<yyyyMMdd>/ 下（移动而非删除，可恢复）：
  · p_id/sales_history/*.json   —— 文件名即日期（YYYY-MM-DD.json）
  · p_id/sellable_gap/*.json    —— 按文件修改时间
  · p_id/sif_lifecycle/*        —— 按文件修改时间

默认 dry-run：只列出会被归档的文件；必须显式 --yes 才移动。

用法：
  python scripts/archive_p_id.py --days 180              # 只列清单
  python scripts/archive_p_id.py --days 180 --yes        # 执行归档
"""
import argparse
import datetime as dt
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

P_ID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "p_id")
ARCHIVE_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_archive", "p_id")


def _sales_history_old(path: str, cutoff: dt.date) -> bool:
    name = os.path.splitext(os.path.basename(path))[0]
    try:
        return dt.date.fromisoformat(name) < cutoff
    except ValueError:
        return False


def _mtime_old(path: str, cutoff_ts: float) -> bool:
    try:
        return os.path.getmtime(path) < cutoff_ts
    except OSError:
        return False


def collect(days: int) -> list[str]:
    cutoff = dt.date.today() - dt.timedelta(days=days)
    cutoff_ts = dt.datetime.combine(cutoff, dt.time.min).timestamp()
    targets: list[str] = []

    sales_dir = os.path.join(P_ID, "sales_history")
    if os.path.isdir(sales_dir):
        targets += [os.path.join(sales_dir, f) for f in os.listdir(sales_dir)
                    if f.endswith(".json") and _sales_history_old(os.path.join(sales_dir, f), cutoff)]

    for sub in ("sellable_gap", "sif_lifecycle"):
        d = os.path.join(P_ID, sub)
        if not os.path.isdir(d):
            continue
        for f in os.listdir(d):
            p = os.path.join(d, f)
            if os.path.isfile(p) and _mtime_old(p, cutoff_ts):
                targets.append(p)
    return sorted(targets)


def main() -> int:
    parser = argparse.ArgumentParser(description="p_id 抓取产物归档（默认 dry-run）")
    parser.add_argument("--days", type=int, default=180, help="保留天数（早于 today-days 的归档）")
    parser.add_argument("--yes", action="store_true", help="确认执行（不加则只列清单）")
    args = parser.parse_args()

    targets = collect(args.days)
    total_mb = round(sum(os.path.getsize(p) for p in targets) / 1024 / 1024, 1) if targets else 0.0
    print(f"保留 {args.days} 天：待归档 {len(targets)} 个文件 / {total_mb} MB")
    for p in targets[:20]:
        print("  -", os.path.relpath(p, os.path.dirname(P_ID)))
    if len(targets) > 20:
        print(f"  ...（其余 {len(targets) - 20} 个省略）")

    if not args.yes:
        print("（dry-run：未移动任何文件；确认请加 --yes）")
        return 0

    dest_dir = os.path.join(ARCHIVE_ROOT, dt.date.today().strftime("%Y%m%d"))
    os.makedirs(dest_dir, exist_ok=True)
    moved = 0
    for p in targets:
        rel = os.path.relpath(p, P_ID).replace(os.sep, "__")
        shutil.move(p, os.path.join(dest_dir, rel))
        moved += 1
    print(f"已归档 {moved} 个文件 -> {dest_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
