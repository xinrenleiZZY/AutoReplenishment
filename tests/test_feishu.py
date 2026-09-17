"""飞书日报模板单元测试"""

import asyncio

from app.integrations.feishu import FeishuNotifier


def _sample_report():
    return {
        "calc_date": "2026-08-05",
        "total_asins": 4476,
        "immediate_count": 24,
        "observe_count": 67,
        "pause_count": 4385,
        "top_alerts": [
            {
                "asin": "B0TEST0001",
                "product_name": "圣诞毛毡树",
                "purchase_score": 95,
                "suggested_qty": 12000,
                "purchase_trigger": "库存不足",
            },
            {
                "asin": "B0TEST0002",
                "product_name": "测试产品二",
                "purchase_score": 90,
                "suggested_qty": 500,
                "purchase_trigger": "临近节日备货窗口",
            },
        ],
        "results": [],
    }


def test_daily_report_post_structure():
    notifier = FeishuNotifier()
    post = notifier.format_daily_report_post(_sample_report())
    assert post["zh_cn"]["title"] == "🚀 自动补货决策日报"

    lines = [line[0].get("text", "") for line in post["zh_cn"]["content"] if line]
    joined = "\n".join(lines)
    assert "📊 扫描 ASIN 4,476" in joined
    assert "🛒 立即采购 24" in joined
    assert "🔔 重点提醒 TOP" in joined
    assert "🥇 圣诞毛毡树" in joined
    assert "🥈 测试产品二" in joined
    assert "采购建议：12,000件" in joined
    assert "分析：库存不足" in joined
    assert "请及时处理补货任务" in joined
    assert "🤖 自动补货决策系统" in joined
    flat = str(post)
    assert '"user_id": "all"' not in flat  # 不再@所有人，改为@负责人


def test_daily_report_post_empty_alerts():
    report = _sample_report()
    report["top_alerts"] = []
    notifier = FeishuNotifier()
    post = notifier.format_daily_report_post(report)
    joined = "\n".join(line[0].get("text", "") for line in post["zh_cn"]["content"] if line)
    assert "今日暂无风险提醒" in joined


def test_daily_report_post_stockout_alerts():
    """断货风险提醒：概览显示数量，风险产品用⚠️标识并展示提醒原因"""
    report = {
        "calc_date": "2026-08-06",
        "total_asins": 164,
        "immediate_count": 0,
        "observe_count": 0,
        "pause_count": 58,
        "stockout_count": 5,
        "pause_stockout_count": 4,
        "top_alerts": [
            {
                "asin": "B0RISK0001",
                "product_name": "断货风险产品A",
                "purchase_level": "暂停",
                "alert_type": "断货风险",
                "alert_reason": "库存仅覆盖7天，低于补货周期45天",
                "purchase_score": 55,
                "suggested_qty": 0,
                "inventory_days": 7,
            },
            {
                "asin": "B0IMM0002",
                "product_name": "立即采购产品B",
                "purchase_level": "立即采购",
                "alert_type": "立即采购",
                "alert_reason": "库存仅覆盖5天，低于补货周期45天",
                "purchase_score": 95,
                "suggested_qty": 300,
                "inventory_days": 5,
            },
        ],
    }
    notifier = FeishuNotifier()
    post = notifier.format_daily_report_post(report)
    joined = "\n".join(line[0].get("text", "") for line in post["zh_cn"]["content"] if line)
    assert "⚠️ 断货风险 5 个（其中暂停产品 4 个）" in joined
    assert "⚠️ 断货风险产品A" in joined
    assert "提醒：断货风险" in joined
    assert "库存仅覆盖7天，低于补货周期45天" in joined
    assert "🥈 立即采购产品B" in joined


def test_daily_report_card_stockout_header():
    """富文本卡片：无立即采购但存在断货风险时，头部红色并展示风险明细"""
    report = _sample_report()
    report["immediate_count"] = 0
    report["stockout_count"] = 3
    report["pause_stockout_count"] = 3
    report["top_alerts"] = [{
        "asin": "B0RISK0001",
        "product_name": "断货风险产品A",
        "purchase_level": "暂停",
        "alert_type": "断货风险",
        "alert_reason": "库存仅覆盖10天，低于危险阈值15天",
        "purchase_score": 50,
        "suggested_qty": 0,
        "inventory_days": 10,
        "operators": ["张三"],
        "feishu_user_ids": ["ou_zhangsan"],
    }]
    notifier = FeishuNotifier(chat_ids=["oc_a"])
    card = notifier.format_daily_report(report)
    assert card["header"]["template"] == "red"
    flat = str(card)
    assert "断货风险: **3**" in flat
    assert "提醒: **断货风险**" in flat
    assert "库存仅覆盖10天，低于危险阈值15天" in flat


def test_daily_report_card_format():
    """富文本卡片：含头部颜色、AI总结、@负责人"""
    report = _sample_report()
    report["top_alerts"][0]["operators"] = ["张三"]
    report["top_alerts"][0]["feishu_user_ids"] = ["ou_zhangsan"]
    report["ai_summary"] = "今日总体平稳，重点处理库存告急产品"
    notifier = FeishuNotifier(chat_ids=["oc_a"])
    card = notifier.format_daily_report(report)
    assert card["config"]["wide_screen_mode"] is True
    assert card["header"]["template"] in ("red", "green")
    flat = str(card)
    assert "ou_zhangsan" in flat
    assert "AI 日报总结" in flat


def test_daily_report_sends_to_whitelist_groups(monkeypatch):
    """群白名单：日报卡片推送到白名单里的每个群"""
    notifier = FeishuNotifier(chat_ids=["oc_a", "oc_b"])
    sent = []

    async def fake_send(msg_type, content, chat_id=None):
        sent.append(chat_id)
        return True

    monkeypatch.setattr(notifier, "_send_via_app", fake_send)
    ok = asyncio.run(notifier.send_daily_report(_sample_report()))
    assert ok is True
    assert sent == ["oc_a", "oc_b"]


def test_chat_ids_whitelist_priority():
    """白名单优先于 FEISHU_CHAT_ID"""
    assert FeishuNotifier(chat_ids=["oc_a", "oc_b"]).chat_ids == ["oc_a", "oc_b"]
    assert len(FeishuNotifier().chat_ids) >= 1  # 使用 .env 白名单/默认群
