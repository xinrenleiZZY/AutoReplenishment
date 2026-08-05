"""列出飞书应用机器人所在的群聊，用于配置 .env 中的 FEISHU_CHAT_ID。

用法:
    python tools/feishu_discover_chats.py
"""

import asyncio
import json

from app.integrations.feishu import FeishuNotifier


async def main():
    notifier = FeishuNotifier()
    if not notifier.app_mode:
        print("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，请在 .env 中填写后重试。")
        return

    chats = await notifier.list_chats()
    if not chats:
        print("未找到机器人所在的群聊。请确认：")
        print("  1. 应用机器人已添加到目标群；")
        print("  2. 应用已开通 im:chat 读取权限；")
        print("  3. 应用已发布或处于可用状态。")
        return

    print(f"找到 {len(chats)} 个群聊：")
    for chat in chats:
        print(json.dumps(
            {
                "chat_id": chat.get("chat_id"),
                "name": chat.get("name"),
                "chat_mode": chat.get("chat_mode"),
            },
            ensure_ascii=False,
        ))


if __name__ == "__main__":
    asyncio.run(main())
