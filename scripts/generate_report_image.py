# -*- coding: utf-8 -*-
"""生成《采购日报 + 重点可视化大屏》图片并发送到飞书群

用法:
  python -m scripts.generate_report_image            # 发送到配置的群
  python -m scripts.generate_report_image --save     # 仅保存 PNG 不发送
  python -m scripts.generate_report_image --out reports/report.png
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.report_image import generate_and_send, generate_image_bytes


async def main():
    args = sys.argv[1:]
    save_only = "--save" in args
    out = "reports/report.png"
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    image_bytes = await generate_image_bytes()
    with open(out, "wb") as f:
        f.write(image_bytes)
    print(f"图片已保存: {out}（{len(image_bytes)} bytes）")

    if not save_only:
        ok = await generate_and_send()
        print("飞书发送:", "成功" if ok else "失败")


if __name__ == "__main__":
    asyncio.run(main())
