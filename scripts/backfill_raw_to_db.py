# -*- coding: utf-8 -*-
"""把 p_id/ 目录现有原始数据文件一次性回填到 api_raw_responses 表

覆盖：showOnline 全量/精简、每日销量快照、SIF 生命周期、标签、包装规格、飞书在途表。

用法：python -m scripts.backfill_raw_to_db
"""

import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.raw_store import save_raw  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P_ID = os.path.join(BASE_DIR, "p_id")


def _load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


async def main():
    tasks = []
    total = 0

    # 1) showOnline 全量/精简
    for name, source in (("msku_id_full.json", "showOnline_full"),
                         ("msku_id_clean_full.json", "showOnline_clean"),
                         ("tags_full.json", "tags_full"),
                         ("package_specs_full.json", "package_specs"),
                         ("sa_analysis.json", "sa_analysis")):
        path = os.path.join(P_ID, name)
        if os.path.exists(path):
            tasks.append(save_raw(source, _load_json(path)))
            total += 1
            print(f"[回填] {name} → {source}")

    # 2) 每日销量快照（fetch_date 取文件名日期）
    hist_dir = os.path.join(P_ID, "sales_history")
    if os.path.isdir(hist_dir):
        for f in sorted(os.listdir(hist_dir)):
            if not f.endswith(".json"):
                continue
            d = f[:-5]
            try:
                fd = date.fromisoformat(d)
            except ValueError:
                fd = date.today()
            tasks.append(save_raw("showOnline_sales_history", _load_json(os.path.join(hist_dir, f)),
                                  fetch_date=fd))
            total += 1
        print(f"[回填] sales_history: {len([t for t in tasks])} 个文件")

    # 3) SIF 生命周期（asin 从文件名取）
    sif_dir = os.path.join(P_ID, "sif_lifecycle")
    if os.path.isdir(sif_dir):
        for f in sorted(os.listdir(sif_dir)):
            if not f.endswith(".json"):
                continue
            asin = f[:-5]
            tasks.append(save_raw("sif_lifecycle", _load_json(os.path.join(sif_dir, f)), asin=asin))
            total += 1
        print(f"[回填] sif_lifecycle: {len([t for t in tasks])} 个文件")

    # 4) 飞书在途货件 CSV（原始文本入库）
    csv_path = os.path.join(P_ID, "feishu_intransit_arrival.csv")
    if os.path.exists(csv_path):
        rows = []
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                rows.append(r)
        tasks.append(save_raw("feishu_intransit", {"rows": rows}))
        total += 1
        print(f"[回填] feishu_intransit_arrival.csv: {len(rows)} 行")

    results = await asyncio.gather(*tasks)
    ok = sum(1 for r in results if r)
    print(f"\n回填完成：共 {total} 个文件，成功写入 {ok}，失败 {total - ok}")


if __name__ == "__main__":
    asyncio.run(main())
