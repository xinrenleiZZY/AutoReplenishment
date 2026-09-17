"""导入「不卖需删除 ASIN 名单」到配置 listing_exclude_asins

用法: python -m scripts.import_exclude_asins [--file docs/不卖需删除ASIN名单.xlsx] [--dry-run]
读取 xlsx 的 ASIN 列（自动识别含 ASIN 的表头，否则取第4列），
与现有 listing_exclude_asins 配置合并后写回（去重、逗号分隔）。
保留列表 listing_keep_asins 优先级最高：名单中出现的保留 ASIN 会被剔除，不进入排除列表。
"""

import argparse
import asyncio
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from app.services import config_service


def _find_file(path: str) -> str:
    if path and os.path.exists(path):
        return path
    cands = glob.glob("docs/*.xlsx")
    for c in cands:
        if "ASIN" in c or "删除" in c or "不卖" in c:
            return c
    return cands[0] if cands else ""


def extract_asins(path: str) -> list:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h or "").strip().upper() for h in rows[0]]
    col = None
    for i, h in enumerate(header):
        if h == "ASIN":
            col = i
            break
    if col is None:
        col = 3  # 默认第4列
    asins = []
    for r in rows[1:]:
        if col < len(r) and r[col]:
            a = str(r[col]).strip().upper()
            if a and a not in asins:
                asins.append(a)
    return asins


async def main():
    parser = argparse.ArgumentParser(description="导入不卖需删除ASIN名单到配置")
    parser.add_argument("--file", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = _find_file(args.file)
    if not path:
        print("未找到名单文件")
        return
    new_asins = extract_asins(path)
    print(f"从 {path} 读取 ASIN: {len(new_asins)} 个")

    async with async_session_factory() as session:
        old_raw = await config_service.get_param(session, "listing_exclude_asins") or ""
        keep_raw = await config_service.get_param(session, "listing_keep_asins") or ""
        old_set = {a.strip().upper() for a in str(old_raw).replace("\n", ",").split(",") if a.strip()}
        keep_set = {a.strip().upper() for a in str(keep_raw).replace("\n", ",").split(",") if a.strip()}
        new_set = set(new_asins)
        hit_keep = sorted(new_set & keep_set)
        merged = sorted((old_set | new_set) - keep_set)
        print(f"合并后: {len(merged)} 个（原 {len(old_set)} + 新 {len(new_set)} - 保留列表剔除 {len(hit_keep)}）")
        if hit_keep:
            print(f"保留列表中的 ASIN（已剔除，不排除）: {hit_keep}")
        if args.dry_run:
            print("[dry-run] 不写库")
            return
        await config_service.set_param(session, "listing_exclude_asins", ",".join(merged))
        print("已写回 listing_exclude_asins")


if __name__ == "__main__":
    asyncio.run(main())
