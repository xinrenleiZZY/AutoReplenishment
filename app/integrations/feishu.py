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

    def __init__(self, chat_ids: list[str] | None = None):
        self.webhook_url = settings.FEISHU_WEBHOOK_URL
        self.app_id = settings.FEISHU_APP_ID
        self.app_secret = settings.FEISHU_APP_SECRET
        self.chat_id = settings.FEISHU_CHAT_ID
        if chat_ids is not None:
            self._chat_ids = chat_ids
        else:
            self._chat_ids = [c.strip() for c in (settings.FEISHU_GROUP_WHITELIST or "").split(",") if c.strip()]
        self._token: str | None = None
        self._token_expire_at: float = 0.0

    @property
    def app_mode(self) -> bool:
        """是否已配置应用机器人凭证"""
        return bool(self.app_id and self.app_secret)

    @property
    def chat_ids(self) -> list[str]:
        """日报推送目标群：群白名单优先，空则回退 FEISHU_CHAT_ID"""
        return self._chat_ids or ([self.chat_id] if self.chat_id else [])

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

    async def send_private_text(self, user_id: str, text: str) -> str | None:
        """私发文本消息给指定用户（单聊）

        通过应用机器人调用 im/v1/messages，receive_id_type=open_id。
        user_id 为运营人员表 feishu_user_id（即飞书 open_id，日报@时也用该值）。
        仅应用机器人模式支持私发；Webhook 模式无法定向发送。

        返回 None 表示发送成功；否则返回失败原因（便于前端展示具体告警，
        如应用可用范围不含该用户 code=230013、用户已离职等）。
        """
        if not self.app_mode:
            logger.warning("飞书私发需应用机器人模式（FEISHU_APP_ID/FEISHU_APP_SECRET）")
            return "飞书应用机器人未配置"
        if not user_id:
            logger.warning("飞书私发缺少目标用户ID（feishu_user_id）")
            return "缺少目标用户ID"

        token = await self._get_tenant_access_token()
        if not token:
            return "获取 tenant_access_token 失败"

        url = f"{OPEN_FEISHU_BASE}/im/v1/messages?receive_id_type=open_id"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "receive_id": user_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                result = resp.json()
        except Exception as e:
            logger.error(f"飞书应用机器人私发消息异常: {e}")
            return f"请求异常: {e}"

        if result.get("code") == 0:
            logger.info("飞书应用机器人私发消息成功 user_id=%s", user_id)
            return None
        # 提取飞书具体错误码/提示，便于定位（如 230013 应用可用范围不含该用户）
        code = result.get("code")
        msg = result.get("msg") or result.get("error", {}).get("code")
        logger.error(f"飞书应用机器人私发消息失败: code={code} msg={msg}")
        return f"飞书返回错误 code={code} msg={msg}"

    async def send_daily_report(self, report_data: dict) -> bool:
        """发送日报到飞书群

        统一使用飞书 API 富文本卡片（interactive）：
        - 应用机器人模式：发送到群白名单中的每个群（可多个）
        - Webhook 模式：发送消息卡片到 Webhook
        """
        card = self.format_daily_report(report_data)
        if self.app_mode:
            targets = self.chat_ids
            if not targets:
                logger.warning("飞书应用机器人未配置接收群（FEISHU_CHAT_ID 或 FEISHU_GROUP_WHITELIST）")
                return False
            ok = False
            for chat_id in targets:
                if await self._send_via_app("interactive", card, chat_id=chat_id):
                    ok = True
            return ok
        if not self.webhook_url:
            logger.warning("飞书通知未配置（缺少 FEISHU_APP_ID/FEISHU_APP_SECRET/FEISHU_CHAT_ID 或 FEISHU_WEBHOOK_URL）")
            return False
        return await self._send_webhook({"msg_type": "interactive", "card": card})

    def format_daily_report_post(self, summary_data: dict) -> dict:
        """将日报格式化为飞书富文本（post）消息（新版模板）"""
        calc_date = summary_data.get("calc_date", "未知")
        total = summary_data.get("total_asins", 0)
        immediate = summary_data.get("immediate_count", 0)
        observe = summary_data.get("observe_count", 0)
        pause = summary_data.get("pause_count", 0)
        stockout = summary_data.get("stockout_count", 0)
        pause_stockout = summary_data.get("pause_stockout_count") or 0
        top_alerts = summary_data.get("top_alerts", [])
        ai_summary = summary_data.get("ai_summary", "")

        medals = ["🥇", "🥈", "🥉"]
        divider = "━━━━━━━━━━━━"

        content = [
            [{"tag": "text", "text": f"📅 自动补货决策日报　{calc_date}"}],
            [{"tag": "text", "text": divider}],
            [{"tag": "text", "text": f"📊 扫描 ASIN {total:,} ｜ 🛒 立即采购 {immediate:,} ｜ 👀 观察 {observe:,} ｜ ⏸ 暂停 {pause:,}"}],
            [{"tag": "text", "text": f"⚠️ 断货风险 {stockout:,} 个（其中暂停产品 {pause_stockout:,} 个）"}],
            [{"tag": "text", "text": divider}],
        ]

        if top_alerts:
            content.append([{"tag": "text", "text": "🔔 重点提醒 TOP" + str(min(len(top_alerts), 10))}])
            for i, alert in enumerate(top_alerts[:10], 1):
                name = alert.get("product_name", "") or alert.get("asin", "")
                qty = alert.get("suggested_qty") or 0
                # 断货风险以规则判定原因为准（AI 分析可能是历史结论，避免与风险提示矛盾）
                reason = (
                    alert.get("alert_reason")
                    if alert.get("alert_type") == "断货风险"
                    else alert.get("ai_analysis") or alert.get("alert_reason") or alert.get("purchase_trigger") or "—"
                )
                level = alert.get("purchase_level") or ""
                alert_type = alert.get("alert_type") or ""
                operators = alert.get("operators") or []
                days = alert.get("inventory_days")
                marker = "⚠️" if alert_type == "断货风险" else (medals[i - 1] if i <= 3 else f"{i}.")
                content.append([{"tag": "text", "text": f"{marker} {name}　({alert.get('asin', '')})"}])
                info = f"采购建议：{qty:,}件"
                if level:
                    info += f"｜级别：{level}"
                if alert_type and alert_type != "立即采购":
                    info += f"｜提醒：{alert_type}"
                if operators:
                    info += f"｜负责人：{'/'.join(operators)}"
                if days is not None:
                    info += f"｜库存：{days}天"
                content.append([{"tag": "text", "text": info}])
                content.append([{"tag": "text", "text": f"　分析：{reason}"}])
                if i < min(len(top_alerts), 10):
                    content.append([{"tag": "text", "text": ""}])
        else:
            content.append([{"tag": "text", "text": "🔔 今日暂无风险提醒，整体库存状况良好"}])

        # AI 日报总结
        if ai_summary:
            content.append([{"tag": "text", "text": divider}])
            content.append([{"tag": "text", "text": "🤖 AI 日报总结"}])
            content.append([{"tag": "text", "text": ai_summary}])

        # @负责人（仅运营人员库中有飞书UID的负责人，其他人不@）
        content.append([{"tag": "text", "text": divider}])
        content.append([{"tag": "text", "text": "👥 负责人跟进提醒"}])
        by_operator: dict[str, dict] = {}
        for alert in top_alerts[:10]:
            for idx, op in enumerate(alert.get("operators") or []):
                uid = (alert.get("feishu_user_ids") or [None] * len(alert.get("operators") or []))[idx] if idx < len(alert.get("feishu_user_ids") or []) else None
                entry = by_operator.setdefault(op, {"uid": uid, "asins": []})
                entry["asins"].append(alert.get("asin", ""))
        for op, entry in by_operator.items():
            line = f"{op}：请处理 {entry['asins'][0]}" + (f" 等{len(entry['asins'])}个产品" if len(entry['asins']) > 1 else "")
            if entry.get("uid"):
                content.append([{"tag": "at", "user_id": entry["uid"]}, {"tag": "text", "text": f" {line}"}])
            else:
                content.append([{"tag": "text", "text": f"（未绑定飞书UID，无法@）{line}"}])
        if not by_operator:
            content.append([{"tag": "text", "text": "今日重点提醒暂无负责人"}])

        content.append([{"tag": "text", "text": divider}])
        content.append([{"tag": "text", "text": "⏰ 请及时处理补货任务"}])
        content.append([{"tag": "text", "text": "🤖 自动补货决策系统 · DeepSeek AI"}])

        return {
            "zh_cn": {
                "title": "🚀 自动补货决策日报",
                "content": content,
            }
        }

    def format_daily_report(self, summary_data: dict) -> dict:
        """格式化为飞书富文本消息卡片（interactive，应用机器人/Webhook 通用）"""
        calc_date = summary_data.get("calc_date", "未知")
        total = summary_data.get("total_asins", 0)
        immediate = summary_data.get("immediate_count", 0)
        observe = summary_data.get("observe_count", 0)
        pause = summary_data.get("pause_count", 0)
        stockout = summary_data.get("stockout_count", 0)
        pause_stockout = summary_data.get("pause_stockout_count") or 0
        top_alerts = summary_data.get("top_alerts", [])
        ai_summary = summary_data.get("ai_summary", "")

        header = {
            "template": "red" if immediate > 0 or stockout > 0 else "green",
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
                if score is None:
                    score = "-"
                base_score = alert.get("base_score")
                qty = alert.get("suggested_qty", 0)
                reason = (
                    alert.get("alert_reason")
                    if alert.get("alert_type") == "断货风险"
                    else alert.get("ai_analysis") or alert.get("alert_reason") or alert.get("purchase_trigger", "")
                )
                level = alert.get("purchase_level", "")
                alert_type = alert.get("alert_type", "")
                operators = alert.get("operators") or []
                days = alert.get("inventory_days")
                marker = "⚠️" if alert_type == "断货风险" else ""
                score_note = f"（原始 {base_score} 分）" if base_score is not None and base_score != score else ""
                alert_text = (
                    f"{marker}{i}. **{name}** ({asin})\n"
                    f"   ┣ 评分: {score} 分{score_note}\n"
                    f"   ┣ 建议采购: {qty} 件"
                )
                if level:
                    alert_text += f" ｜ 级别: **{level}**"
                if alert_type and alert_type != "立即采购":
                    alert_text += f" ｜ 提醒: **{alert_type}**"
                if operators:
                    alert_text += f" ｜ 负责人: {'/'.join(operators)}"
                if days is not None:
                    alert_text += f" ｜ 库存: {days}天"
                alert_text += f"\n   ┗ 分析: {reason}\n"
                alert_elements.append({
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": alert_text},
                })
        else:
            alert_elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": "**🔔 重点提醒**\n今日暂无风险提醒，整体库存状况良好"},
            })

        # AI 日报总结
        if ai_summary:
            alert_elements.append({"tag": "hr"})
            alert_elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**🤖 AI 日报总结**\n{ai_summary}"},
            })

        # @负责人（仅运营人员库中有飞书UID的负责人）
        by_operator: dict[str, dict] = {}
        for alert in top_alerts[:10]:
            for idx, op in enumerate(alert.get("operators") or []):
                uids = alert.get("feishu_user_ids") or []
                uid = uids[idx] if idx < len(uids) else None
                entry = by_operator.setdefault(op, {"uid": uid, "asins": []})
                entry["asins"].append(alert.get("asin", ""))
        at_content = "**👥 负责人跟进提醒**\n"
        if by_operator:
            for op, entry in by_operator.items():
                if entry.get("uid"):
                    at_content += f"<at id={entry['uid']}></at> {op}：请处理 {entry['asins'][0]}"
                    if len(entry["asins"]) > 1:
                        at_content += f" 等{len(entry['asins'])}个产品"
                    at_content += "\n"
                else:
                    at_content += f"（未绑定飞书UID，无法@）{op}：{entry['asins'][0]}\n"
        else:
            at_content += "今日重点提醒暂无负责人\n"
        alert_elements.append({"tag": "hr"})
        alert_elements.append({"tag": "div", "text": {"tag": "lark_md", "content": at_content}})

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
                            f"> ⚠️ 断货风险: **{stockout}**（其中暂停产品 **{pause_stockout}**）\n"
                        ),
                    },
                },
                {"tag": "hr"},
                *alert_elements,
            ],
        }

    async def _upload_image(self, image_bytes: bytes) -> str | None:
        """上传图片到飞书，返回 image_key（应用机器人）"""
        token = await self._get_tenant_access_token()
        if not token:
            return None
        url = f"{OPEN_FEISHU_BASE}/im/v1/images"
        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    url, headers=headers,
                    data={"image_type": "message"},
                    files={"image": ("report.png", image_bytes, "image/png")},
                )
                resp.raise_for_status()
                result = resp.json()
        except Exception as e:
            logger.error(f"飞书上传图片异常: {e}")
            return None
        if result.get("code") == 0:
            return (result.get("data") or {}).get("image_key")
        logger.error(f"飞书上传图片失败: {result}")
        return None

    async def send_image(self, image_bytes: bytes, chat_id: str | None = None) -> bool:
        """发送图片消息（日报可视化大屏）到群（应用机器人；Webhook 模式不支持图片，返回 False）"""
        if not self.app_mode:
            logger.warning("飞书图片消息需应用机器人模式（FEISHU_APP_ID/FEISHU_APP_SECRET）")
            return False
        image_key = await self._upload_image(image_bytes)
        if not image_key:
            return False
        targets = [chat_id] if chat_id else self.chat_ids
        if not targets:
            logger.warning("飞书应用机器人未配置接收群（FEISHU_CHAT_ID 或 FEISHU_GROUP_WHITELIST）")
            return False
        ok = False
        for cid in targets:
            if await self._send_via_app("image", {"image_key": image_key}, chat_id=cid):
                ok = True
        return ok

    async def _send_via_app(self, msg_type: str, content: dict, chat_id: str | None = None) -> bool:
        """通过应用机器人发送消息到群（im/v1/messages，receive_id_type=chat_id）"""
        chat_id = chat_id or self.chat_id
        if not chat_id:
            logger.warning("飞书应用机器人未配置接收群 FEISHU_CHAT_ID")
            return False

        token = await self._get_tenant_access_token()
        if not token:
            return False

        url = f"{OPEN_FEISHU_BASE}/im/v1/messages?receive_id_type=chat_id"
        headers = {"Authorization": f"Bearer {token}"}
        payload = {
            "receive_id": chat_id,
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
