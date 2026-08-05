"""飞书通知集成（应用机器人 / 自定义机器人 Webhook 双模式）"""

import json
import logging
import time

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

OPEN_FEISHU_BASE = "https://open.feishu.cn/open-apis"


class FeishuNotifier:
    """飞书机器人通知

    优先使用应用机器人（FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CHAT_ID），
    通过 tenant_access_token 调用 im/v1/messages 发送消息；
    未配置应用机器人时，回退到自定义机器人 Webhook（FEISHU_WEBHOOK_URL）。
    """

    def __init__(self):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL
        self.app_id = settings.FEISHU_APP_ID
        self.app_secret = settings.FEISHU_APP_SECRET
        self.chat_id = settings.FEISHU_CHAT_ID
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    @property
    def app_mode(self) -> bool:
        """是否已配置应用机器人凭证"""
        return bool(self.app_id and self.app_secret)

    async def _get_tenant_access_token(self) -> str | None:
        """获取应用机器人 tenant_access_token（内存缓存，过期前 60s 自动刷新）"""
        now = time.time()
        if self._token and self._token_expire_at > now + 60:
            return self._token

        url = f"{OPEN_FEISHU_BASE}/auth/v3/tenant_access_token/internal"
        payload = {"app_id": self.app_id, "app_secret": self.app_secret}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                result = resp.json()
        except Exception as e:
            logger.error(f"获取飞书 tenant_access_token 失败: {e}")
            return None

        if result.get("code") != 0:
            logger.error(f"获取飞书 tenant_access_token 失败: {result}")
            return None

        self._token = result["tenant_access_token"]
        self._token_expire_at = now + int(result.get("expire", 7200))
        logger.info("飞书 tenant_access_token 获取成功")
        return self._token

    async def list_chats(self) -> list[dict]:
        """列出应用机器人所在的群聊（用于发现接收群 FEISHU_CHAT_ID）"""
        token = await self._get_tenant_access_token()
        if not token:
            return []

        url = f"{OPEN_FEISHU_BASE}/im/v1/chats?page_size=50"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                result = resp.json()
        except Exception as e:
            logger.error(f"获取飞书群聊列表失败: {e}")
            return []

        if result.get("code") != 0:
            logger.error(f"获取飞书群聊列表失败: {result}")
            return []
        return result.get("data", {}).get("items", [])

    async def send_text(self, text: str) -> bool:
        """发送文本消息"""
        if self.app_mode:
            return await self._send_via_app("text", {"text": text})
        if not self.webhook_url:
            logger.warning("飞书通知未配置（缺少 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID 或 FEISHU_WEBHOOK_URL）")
            return False
        return await self._send_webhook({"msg_type": "text", "content": {"text": text}})

    async def send_daily_report(self, report_data: dict) -> bool:
        """发送日报到飞书群

        应用机器人模式发送富文本（post）消息；Webhook 模式发送消息卡片（interactive）。
        """
        if self.app_mode:
            post = self.format_daily_report_post(report_data)
            return await self._send_via_app("post", post)
        if not self.webhook_url:
            logger.warning("飞书通知未配置（缺少 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID 或 FEISHU_WEBHOOK_URL）")
            return False
        card = self.format_daily_report(report_data)
        return await self._send_webhook({"msg_type": "interactive", "card": card})

    def format_daily_report_post(self, summary_data: dict) -> dict:
        """将日报格式化为飞书富文本（post）消息（新版模板）"""
        calc_date = summary_data.get("calc_date", "未知")
        total = summary_data.get("total_asins", 0)
        immediate = summary_data.get("immediate_count", 0)
        observe = summary_data.get("observe_count", 0)
        pause = summary_data.get("pause_count", 0)
        top_alerts = summary_data.get("top_alerts", [])

        medals = ["🥇", "🥈", "🥉"]
        divider = "━━━━━━━━━━━━"

        content = [
            [{"tag": "text", "text": f"📅 {calc_date}"}],
            [{"tag": "text", "text": divider}],
            [{"tag": "text", "text": "📊 今日扫描"}],
            [{"tag": "text", "text": f"ASIN总数：{total:,}"}],
            [{"tag": "text", "text": f"🛒 立即采购：{immediate:,}"}],
            [{"tag": "text", "text": f"👀 观察：{observe:,}"}],
            [{"tag": "text", "text": f"⏸ 暂停：{pause:,}"}],
            [{"tag": "text", "text": divider}],
        ]

        if top_alerts:
            content.append([{"tag": "text", "text": "🔔 重点提醒 TOP10"}])
            for i, alert in enumerate(top_alerts[:10], 1):
                name = alert.get("product_name", "") or alert.get("asin", "")
                qty = alert.get("suggested_qty") or 0
                reason = alert.get("purchase_trigger") or "—"
                marker = medals[i - 1] if i <= 3 else f"{i}."
                content.append([{"tag": "text", "text": f"{marker} {name}"}])
                content.append([{"tag": "text", "text": f"采购建议：{qty:,}件"}])
                content.append([{"tag": "text", "text": f"原因：{reason}"}])
                if i < min(len(top_alerts), 10):
                    content.append([{"tag": "text", "text": ""}])
        else:
            content.append([{"tag": "text", "text": "🔔 今日暂无立即采购提醒"}])

        content.append([{"tag": "text", "text": divider}])
        content.append([{"tag": "text", "text": "请及时处理补货任务"}])
        content.append([{"tag": "text", "text": "🤖 自动补货决策系统"}])
        content.append([{"tag": "at", "user_id": "all"}])

        return {
            "zh_cn": {
                "title": "🚀 自动补货决策日报",
                "content": content,
            }
        }

    def format_daily_report(self, summary_data: dict) -> dict:
        """格式化为飞书消息卡片（Webhook 模式使用）"""
        calc_date = summary_data.get("calc_date", "未知")
        total = summary_data.get("total_asins", 0)
        immediate = summary_data.get("immediate_count", 0)
        observe = summary_data.get("observe_count", 0)
        pause = summary_data.get("pause_count", 0)
        top_alerts = summary_data.get("top_alerts", [])

        header = {
            "title": {"tag": "plain_text", "content": f"📊 自动补货决策日报 {calc_date}"},
        }

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

        return {
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

    async def _send_via_app(self, msg_type: str, content: dict) -> bool:
        """通过应用机器人发送消息到群（im/v1/messages，receive_id_type=chat_id）"""
        if not self.chat_id:
            logger.warning("飞书应用机器人未配置接收群 FEISHU_CHAT_ID")
            return False

        token = await self._get_tenant_access_token()
        if not token:
            return False

        url = f"{OPEN_FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "receive_id": self.chat_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                result = resp.json()
        except Exception as e:
            logger.error(f"飞书应用机器人发送消息异常: {e}")
            return False

        if result.get("code") == 0:
            logger.info("飞书应用机器人消息发送成功")
            return True
        logger.error(f"飞书应用机器人消息发送失败: {result}")
        return False

    async def _send_webhook(self, payload: dict) -> bool:
        """通过自定义机器人 Webhook 发送消息"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.webhook_url, json=payload)
                resp.raise_for_status()
                result = resp.json()
                if result.get("code") == 0:
                    logger.info("飞书消息发送成功")
                    return True
                logger.error(f"飞书消息发送失败: {result}")
                return False
        except Exception as e:
            logger.error(f"飞书消息发送异常: {e}")
            return False
