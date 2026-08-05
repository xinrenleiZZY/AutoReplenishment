# -*- coding: utf-8 -*-
"""
元素选择器加载工具
职责：从 V12 元素提取器生成的 JSON 文件中读取并列出所有选择器
用途：在调试时快速查看当前有哪些可用的选择器，对比提取结果

使用方式:
    python selector_loader.py                          # 列出 docs/cleaned/ 下所有 JSON
    python selector_loader.py lingxing_yy-029          # 查看指定文件的提取步骤
    python selector_loader.py lingxing_yy-029 --text   # 仅显示 text 策略的选择器
"""
import sys
import json
import os
import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEANED_DIR = os.path.join(PROJECT_ROOT, "docs", "cleaned")


def list_available_files():
    """列出 docs/cleaned/ 下所有 JSON 文件"""
    files = glob.glob(os.path.join(CLEANED_DIR, "*_cleaned.json"))
    if not files:
        print("未找到任何 JSON 选择器文件")
        print(f"目录: {CLEANED_DIR}")
        return

    print(f"可用选择器文件 ({len(files)}):")
    for f in sorted(files):
        name = os.path.basename(f).replace("_cleaned.json", "")
        with open(f, "r", encoding="utf-8") as fh:
            steps = json.load(fh)
        print(f"  {name:<35} ({len(steps)} 步骤)")
    print(f"\n目录: {CLEANED_DIR}")


def show_selector_file(filename, text_only=False):
    """展示指定 JSON 文件的选择器内容"""
    # 支持给不带后缀的名、带 _cleaned 的名
    candidates = [
        f"{filename}.json",
        f"{filename}_cleaned.json",
        filename,
    ]
    filepath = None
    for c in candidates:
        full = os.path.join(CLEANED_DIR, c) if not os.path.isabs(c) else c
        if os.path.exists(full):
            filepath = full
            break

    if not filepath:
        print(f"文件不存在: {filename}")
        print(f"查找目录: {CLEANED_DIR}")
        return

    with open(filepath, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    print(f"文件: {os.path.basename(filepath)}")
    print(f"步骤数: {len(data)}")
    print("=" * 70)

    for idx, step in enumerate(data, 1):
        step_name = step.get("step_name", f"step-{idx}")
        action = step.get("action_type", "")
        url = step.get("page_url", "")
        title = step.get("page_title", "")
        cleaned = step.get("cleaned", {})

        best = cleaned.get("best_strategy", {})
        text_val = best.get("value", "") if best.get("type") == "text" else ""

        # 提取所有策略
        strategies = cleaned.get("strategies", [])

        # 只显示 text 策略
        if text_only:
            text_strategies = [s for s in strategies if s.get("type") == "text"]
            if not text_strategies:
                continue

        print(f"\n[{idx}] {step_name} | {action}")
        print(f"    页面: {title}")
        print(f"    URL: {url}")
        print(f"    标签: {step['selectors']['tag']}")
        if step['selectors']['inner_text']:
            print(f"    文本: {step['selectors']['inner_text'][:60]}")

        print(f"    策略 ({len(strategies)} 种):")
        for s in strategies:
            stype = s.get("type", "?")
            sval = s.get("value", s.get("anchor", s.get("target", "")))
            desc = s.get("description", "")
            marker = " ← BEST" if s == best else ""
            if isinstance(sval, dict):
                sval = f"{sval.get('type')}={sval.get('value')} / {s.get('target', {}).get('value', '')}"
            print(f"      [{stype:>10}] {sval}{marker}")

        if text_only:
            continue

        # Playwright 原生选择器
        pw = step['selectors'].get('playwright_selector', '')
        if pw:
            print(f"    Playwright: {pw[:100]}")

        # CSS 选择器
        css = step['selectors'].get('css_selector', '')
        if css and len(css) < 120:
            print(f"    CSS: {css}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        list_available_files()
    else:
        text_only = "--text" in sys.argv
        # 过滤掉 --text 参数
        args = [a for a in sys.argv[1:] if not a.startswith("--")]
        filename = args[0] if args else None
        if filename:
            show_selector_file(filename, text_only=text_only)
        else:
            list_available_files()
