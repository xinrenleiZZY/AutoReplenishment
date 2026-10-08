"""手动触发指定等级产品计算并推送飞书日报（定时链路之外可立即运行）

用法:
    python -m scripts.daily_sab_report SAB
    python -m scripts.daily_sab_report --level S,A,B
    python -m scripts.daily_sab_report SAB --no-ai --no-image --all-levels
    python -m scripts.daily_sab_report S,A --lifecycles 启动期,增长期,热卖期

流程:
    1) 仅对传入等级（默认 SAB）的启用产品立即计算（忽略频率，多等级用 run_level_calculation）；
    2) 基于当日计算结果生成日报摘要（默认只统计本次计算的等级，--all-levels 放宽为全等级）；
    3) AI 日报总结（DEEPSEEK_AI_EVAL_ENABLED=true 时自动生成，--no-ai 跳过）；
    4) 推送飞书日报卡片，应用机器人模式下附带日报大屏图片（--no-image 跳过）。

可选 --lifecycles 按生命周期过滤（如 启动期,增长期,热卖期），计算与日报摘要均沿用该过滤。
"""

import argparse
import asyncio
import json
import logging

from app.database import async_session_factory
from app.integrations.feishu import FeishuNotifier
from app.services import ai_eval
from app.tasks.calculation_tasks import (
    _normalize_levels,
    get_daily_summary,
    run_level_calculation,
)

logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="立即计算指定等级产品并推送飞书日报")
    parser.add_argument(
        "level", nargs="?", default="SAB",
        help="要计算的产品等级，支持 SAB / S,A,B（默认 SAB）",
    )
    parser.add_argument(
        "--all-levels", action="store_true",
        help="日报统计当日全部等级结果（默认只统计本次计算的等级）",
    )
    parser.add_argument(
        "--lifecycles", default=None,
        help="按生命周期过滤，逗号分隔，如 启动期,增长期,热卖期（默认全部）",
    )
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI 日报总结")
    parser.add_argument("--no-image", action="store_true", help="跳过日报大屏图片")
    return parser.parse_args()


async def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = parse_args()
    levels = _normalize_levels(args.level)
    if not levels:
        print(f"无效产品等级: {args.level}，支持 S/A/B/C/D（如 SAB 或 S,A,B）")
        return 1
    level_arg = ",".join(levels)
    print(f"STEP1 计算等级 {levels}（共 {len(levels)} 个）"
          + (f"，生命周期过滤 {args.lifecycles}" if args.lifecycles else ""))

    # 1) 按等级立即计算（忽略频率）
    stats = await run_level_calculation(level_arg, lifecycles=args.lifecycles)
    print("CALC_STATS:", json.dumps({
        "total": stats.get("total"),
        "success": stats.get("success"),
        "failed": stats.get("failed"),
        "immediate": stats.get("immediate"),
        "observe": stats.get("observe"),
        "pause": stats.get("pause"),
        "errors": stats.get("errors"),
        "by_level": stats.get("by_level"),
    }, ensure_ascii=False, default=str))
    if stats.get("failed", 0) > 0:
        logger.warning("计算存在失败产品 %d 个，继续生成日报", stats["failed"])

    # 2) 生成日报摘要（默认只统计本次计算的等级）
    async with async_session_factory() as session:
        summary = await get_daily_summary(
            session,
            levels=None if args.all_levels else level_arg,
            lifecycles=args.lifecycles,
        )
    print("SUMMARY:", json.dumps({
        "calc_date": summary.get("calc_date"),
        "data_date": summary.get("data_date"),
        "data_scope": summary.get("data_scope"),
        "total_asins": summary.get("total_asins"),
        "immediate_count": summary.get("immediate_count"),
        "observe_count": summary.get("observe_count"),
        "pause_count": summary.get("pause_count"),
        "stockout_count": summary.get("stockout_count"),
        "overstock_count": summary.get("overstock_count"),
        "top_alerts": len(summary.get("top_alerts") or []),
    }, ensure_ascii=False, default=str))

    # 3) AI 日报总结（失败不影响推送）
    if ai_eval.ai_enabled() and not args.no_ai:
        try:
            async with async_session_factory() as session:
                summary = await ai_eval.generate_daily_report_ai(summary, session)
            print("AI_SUMMARY_OK")
        except Exception as e:  # noqa: BLE001
            logger.warning("AI 日报生成失败，使用规则日报推送: %s", e)

    # 4) 推送飞书
    notifier = FeishuNotifier()
    if not notifier.app_mode and not notifier.webhook_url:
        print("飞书通知未配置：请在 .env 设置 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID 或 FEISHU_WEBHOOK_URL")
        return 1

    ok = await notifier.send_daily_report(summary)
    print("PUSH_OK:", ok)
    if not ok:
        print("飞书日报推送失败")
        return 1

    # 日报大屏图片（失败不影响文本日报推送结果）
    image_ok = False
    if notifier.app_mode and not args.no_image:
        try:
            from app.services.report_image import generate_and_send
            image_ok = await generate_and_send(notifier)
        except Exception as e:  # noqa: BLE001
            logger.error("日报大屏图片发送失败: %s", e)
        print("IMAGE_OK:", image_ok)

    print("DAILY_DONE", json.dumps({
        "levels": levels,
        "push_ok": ok,
        "image_ok": image_ok,
    }, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
