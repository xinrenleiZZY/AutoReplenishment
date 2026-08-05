# -*- coding: utf-8 -*-
"""
元素选择器可靠性检查工具
用途：遍历 docs/cleaned/ 下的 JSON，统计每个步骤的可靠性等级和最佳策略
       输出可用的 Playwright 选择器建议

使用方式:
    python check_selectors.py                          # 检查全部
    python check_selectors.py lingxing_yy-029          # 检查单个文件
"""
import sys
import json
import os
import glob

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEANED_DIR = os.path.join(PROJECT_ROOT, "docs", "cleaned")


def analyze_reliability(filepath):
    """分析单个 JSON 文件的选择器可靠性"""
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    filename = os.path.basename(filepath)
    
    high_count = 0
    medium_count = 0
    low_count = 0
    total = len(data)

    print(f"\n{'='*70}")
    print(f"文件: {filename} ({total} 步骤)")
    print(f"{'='*70}")

    for idx, step in enumerate(data, 1):
        step_name = step.get("step_name", f"step-{idx}")
        action = step.get("action_type", "?")
        cleaned = step.get("cleaned", {})
        reliability = cleaned.get("reliability", "unknown")
        best = cleaned.get("best_strategy", {})

        # 统计
        if reliability == "high":
            high_count += 1
        elif reliability == "medium":
            medium_count += 1
        else:
            low_count += 1

        # 提取所有可用的 Playwright 选择器
        pw_selector = step.get("selectors", {}).get("playwright_selector", "")
        inner_text = step.get("selectors", {}).get("inner_text", "")
        placeholder = step.get("selectors", {}).get("placeholder", "")
        name = step.get("selectors", {}).get("name", "")
        tag = step.get("selectors", {}).get("tag", "")
        css = step.get("selectors", {}).get("css_selector", "")

        # 推荐选择器（按优先级排列）
        recommended = []
        
        # 1. 如果有 inner_text 且是 text 策略 → get_by_text
        if best.get("type") == "text" and best.get("value"):
            recommended.append(f'page.get_by_text("{best["value"]}", exact=True)')
        
        # 2. 如果有 placeholder
        if best.get("type") == "placeholder":
            recommended.append(f'page.get_by_placeholder("{best["value"]}")')
        
        # 3. anchor + css/text
        if best.get("type") == "anchored":
            anchor = best.get("anchor", {})
            target = best.get("target", {})
            if anchor.get("type") == "id":
                recommended.append(f'locator("#{anchor["value"]}").locator(...)')
        
        # 4. 如果 best 是 css
        if best.get("type") == "css":
            recommended.append(best.get("value", ""))

        reliability_icon = {
            "high": "[HIGH]",
            "medium": "[MEDIUM]",
            "low": "[LOW]",
        }.get(reliability, "[UNKNOWN]")

        print(f"\n[{idx}] {step_name} | {action}")
        print(f"    可靠性: {reliability_icon}")
        
        if recommended:
            for r in recommended[:2]:  # 最多显示2个
                print(f"    推荐: {r}")
        else:
            # 从原始选择器里提取可用的
            if pw_selector:
                print(f"    Playwright: {pw_selector}")
            if inner_text:
                print(f"    文本: {inner_text}")
            if placeholder:
                print(f"    Placeholder: {placeholder}")

    # 汇总
    print(f"\n{'─'*70}")
    print(f"可靠性汇总:")
    print(f"  [HIGH]:   {high_count}/{total} ({high_count/total*100:.0f}%)")
    print(f"  [MEDIUM]: {medium_count}/{total}")
    print(f"  [LOW]:    {low_count}/{total}")
    print(f"  结论: {'OK 可以直接使用' if high_count/total >= 0.8 else 'WARN 需要人工核对部分选择器'}")
    print(f"{'─'*70}")

    return {
        "high": high_count,
        "medium": medium_count,
        "low": low_count,
        "total": total,
    }


def check_all():
    """检查所有 JSON 文件"""
    files = glob.glob(os.path.join(CLEANED_DIR, "*_cleaned.json"))
    if not files:
        print(f"未找到 JSON 文件: {CLEANED_DIR}")
        return

    print(f"找到 {len(files)} 个选择器文件\n")
    total_high = 0
    total_all = 0

    for f in sorted(files):
        result = analyze_reliability(f)
        total_high += result["high"]
        total_all += result["total"]

    print(f"\n{'='*70}")
    print(f"全局汇总: {total_high}/{total_all} 步骤高可靠 ({total_high/total_all*100:.0f}%)")
    if total_high / total_all >= 0.8:
        print("整体结论: OK 选择器可靠性高，可以直接集成")
    else:
        print("整体结论: WARN 部分选择器需要人工核对")
    print(f"{'='*70}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        analyze_reliability(sys.argv[1])
    else:
        check_all()
