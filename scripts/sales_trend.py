# -*- coding: utf-8 -*-
"""
销量趋势分析脚本
读取 p_id/sales_history/ 中的历史快照，计算每个 ASIN 的销量趋势。

用法:
    python scripts/sales_trend.py                          # 分析所有历史数据
    python scripts/sales_trend.py --days 14                # 只用最近14天
    python scripts/sales_trend.py --min-days 7             # 至少7天数据才分析
    python scripts/sales_trend.py --output p_id/sales_trend.json

趋势判断逻辑:
  增长率 ≥ 50%  →  快速增长
  增长率 20~50% →  增长
  增长率 -20~20% → 平稳
  增长率 -50~-20% → 下降
  增长率 < -50%  →  快速下降
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SALES_HISTORY_DIR = os.path.join(BASE_DIR, "p_id", "sales_history")
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "p_id", "sales_trend.json")


def load_all_snapshots() -> dict:
    """加载所有历史快照，返回 {date: {asin: sales_data}}"""
    if not os.path.exists(SALES_HISTORY_DIR):
        print(f"错误: 快照目录不存在: {SALES_HISTORY_DIR}")
        print("请先运行: python scripts/daily_sales_snapshot.py")
        return {}

    files = sorted([f for f in os.listdir(SALES_HISTORY_DIR) if f.endswith(".json")])
    if not files:
        print("错误: 无历史快照数据")
        return {}

    snapshots = {}
    for f in files:
        date_str = f.replace(".json", "")
        path = os.path.join(SALES_HISTORY_DIR, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            snapshots[date_str] = data.get("data", {})
        except Exception as e:
            print(f"  警告: 读取 {f} 失败: {e}")

    return snapshots


def calc_growth_rate(values: list) -> float:
    """
    计算增长率
    使用最近1/3周期的均值 vs 最早1/3周期的均值
    例如30天数据: 最近10天均值 / 最早10天均值
    """
    if len(values) < 7:
        return 0.0

    n = len(values)
    period = max(n // 3, 3)

    recent = values[-period:]
    early = values[:period]

    avg_recent = sum(recent) / len(recent)
    avg_early = sum(early) / len(early)

    if avg_early == 0:
        return 100.0 if avg_recent > 0 else 0.0

    return ((avg_recent - avg_early) / avg_early) * 100


def classify_trend(growth_rate: float) -> str:
    """根据增长率分类趋势"""
    if growth_rate >= 50:
        return "快速增长"
    elif growth_rate >= 20:
        return "增长"
    elif growth_rate > -20:
        return "平稳"
    elif growth_rate > -50:
        return "下降"
    else:
        return "快速下降"


def analyze(snapshots: dict, metric: str = "yesterday_volume",
            min_days: int = 7, days_limit: int = None):
    """
    分析销量趋势

    Args:
        snapshots: {date: {asin: data}}
        metric: 分析指标 (yesterday_volume / thirty_volume / category_rank)
        min_days: 最少需要多少天数据才分析
        days_limit: 只使用最近多少天的数据
    """
    dates = sorted(snapshots.keys())
    if days_limit:
        dates = dates[-days_limit:]

    print(f"数据天数: {len(dates)} ({dates[0]} ~ {dates[-1]})")
    print(f"分析指标: {metric}")

    # 按 ASIN 聚合时间序列
    asin_series = {}  # {asin: [values]}

    for date_str in dates:
        day_data = snapshots[date_str]
        for asin, sales in day_data.items():
            val = sales.get(metric, 0)
            if isinstance(val, (int, float)):
                if asin not in asin_series:
                    asin_series[asin] = {"values": [], "msku": sales.get("msku", "")}
                asin_series[asin]["values"].append(val)

    # 计算趋势
    results = {}
    trend_counts = {"快速增长": 0, "增长": 0, "平稳": 0, "下降": 0, "快速下降": 0}

    for asin, info in asin_series.items():
        values = info["values"]
        # 过滤前导零（产品可能刚开始没销量）
        values = [v for v in values if v > 0]
        if len(values) < min_days:
            continue

        growth_rate = calc_growth_rate(values)
        trend = classify_trend(growth_rate)

        results[asin] = {
            "msku": info["msku"],
            "trend": trend,
            "growth_rate": round(growth_rate, 1),
            "avg_recent": round(sum(values[-max(len(values)//3, 3):]) / max(len(values)//3, 3), 1),
            "avg_early": round(sum(values[:max(len(values)//3, 3)]) / max(len(values)//3, 3), 1),
            "data_points": len(values),
        }
        trend_counts[trend] = trend_counts.get(trend, 0) + 1

    # 汇总
    print(f"\n趋势分布:")
    for t, c in sorted(trend_counts.items(), key=lambda x: -x[1]):
        pct = c / len(results) * 100 if results else 0
        print(f"  {t}: {c} ({pct:.1f}%)")
    print(f"  总计: {len(results)} ASINs")

    return results


def main():
    parser = argparse.ArgumentParser(description="销量趋势分析")
    parser.add_argument("--metric", default="yesterday_volume",
                        choices=["yesterday_volume", "thirty_volume",
                                 "average_thirty_volume", "category_rank"],
                        help="分析指标")
    parser.add_argument("--days", type=int, default=None,
                        help="只使用最近N天数据")
    parser.add_argument("--min-days", type=int, default=7,
                        help="最少需要多少天数据才参与分析")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help=f"输出路径 (默认: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    print("=" * 50)
    print("销量趋势分析")
    print("=" * 50)

    snapshots = load_all_snapshots()
    if not snapshots:
        return

    results = analyze(snapshots,
                      metric=args.metric,
                      min_days=args.min_days,
                      days_limit=args.days)

    # 输出
    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "analysis_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "metric": args.metric,
            "total_asins": len(results),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n输出: {output_path}")

    # 打印几个示例
    print(f"\n趋势示例:")
    sorted_by_rate = sorted(results.items(),
                            key=lambda x: abs(x[1]["growth_rate"]), reverse=True)
    for asin, info in sorted_by_rate[:5]:
        print(f"  {asin} ({info['msku'][:20]}): {info['trend']} "
              f"(增长率={info['growth_rate']:+.1f}%, "
              f"近期均值={info['avg_recent']}, 早期均值={info['avg_early']})")


if __name__ == "__main__":
    main()
