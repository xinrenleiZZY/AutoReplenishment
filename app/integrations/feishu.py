"""飞书通知集成"""

import json
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class FeishuNotifier:
    """飞书机器人通知"""

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL

    async def send_text(self, text: str) -> bool:
        """发送文本消息"""
        if not self.webhook_url:
            logger.warning("飞书Webhook未配置")
            return False

        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        return await self._send(payload)

    async def send_daily_report(self, report_data: dict) -> bool:
        """发送日报到飞书群（使用消息卡片格式）"""
        if not self.webhook_url:
            logger.warning("飞书Webhook未配置")
            return False

        card = self.format_daily_report(report_data)
        payload = {
            "msg_type": "interactive",
            "card": card,
        }
        return await self._send(payload)

    def format_daily_report(self, summary_data: dict) -> dict:
        """格式化为飞书消息卡片"""
        calc_date = summary_data.get("calc_date", "未知")
        total = summary_data.get("total_asins", 0)
        immediate = summary_data.get("immediate_count", 0)
        observe = summary_data.get("observe_count", 0)
        pause = summary_data.get("pause_count", 0)
        top_alerts = summary_data.get("top_alerts", [])

        # 构建标题
        header = {
            "title": {"tag": "plain_text", "content": f"📊 自动补货决策日报 {calc_date}"},
        }

        # 构建汇总字段
        summary_fields = [
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**总ASIN数**\n{total}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**🛒 立即采购**\n{immediate}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**👀 观察**\n{observe}"},
            },
            {
                "is_short": True,
                "text": {"tag": "lark_md", "content": f"**⏸ 暂停**\n{pause}"},
            },
        ]

        # 构建重点ASIN表格
        alert_elements = []
        if top_alerts:
            alert_header = {
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**🔔 重点提醒（前{len(top_alerts)}个）**"},
            }
            alert_elements.append(alert_header)

            for i, alert in enumerate(top_alerts, 1):
                asin = alert.get("asin", "")
                name = alert.get("product_name", "")
                score = alert.get("purchase_score", "")
                qty = alert.get("suggested_qty", 0)
                reason = alert.get("purchase_trigger", "")
                alert_text = (
                    f"{i}. **{name}** ({asin})\n"
                    f"   ┣ 评分: {score} 分\n"
                    f"   ┣ 建议采购: {qty} 件\n"
                    f"   ┗ 原因: {reason}\n"
                )
                alert_elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": alert_text},
                })

        # @所有人
        at_all = {
            "tag": "div",
            "text": {"tag": "lark_md", "content": "<at id=all></at>"},
        }
        alert_elements.append(at_all)

        card = {
            "config": {"wide_screen_mode": True},
            "header": header,
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**📈 日报概览**\n"
                            f"> 总ASIN数: **{total}**\n"
                            f"> 🛒 立即采购: **{immediate}** | 👀 观察: **{observe}** | ⏸ 暂停: **{pause}**\n"
                        ),
                    },
                },
                {"tag": "hr"},
                *alert_elements,
            ],
        }

        return card

    async def _send(self, payload: dict) -> bool:
        """发送消息"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                resp.raise_for_status()
                result = resp.json()
                if result.get("code") == 0:
                    logger.info("飞书消息发送成功")
                    return True
                else:
                    logger.error(f"飞书消息发送失败: {result}")
                    return False
        except Exception as e:
            logger.error(f"飞书消息发送异常: {e}")
            return False
