"""飞书日报模板单元测试"""

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
    assert "📊 今日扫描" in joined
    assert "ASIN总数：4,476" in joined
    assert "🛒 立即采购：24" in joined
    assert "🔔 重点提醒 TOP10" in joined
    assert "🥇 圣诞毛毡树" in joined
    assert "🥈 测试产品二" in joined
    assert "采购建议：12,000件" in joined
    assert "原因：库存不足" in joined
    assert "请及时处理补货任务" in joined
    assert "🤖 自动补货决策系统" in joined
    assert post["zh_cn"]["content"][-1] == [{"tag": "at", "user_id": "all"}]


def test_daily_report_post_empty_alerts():
    report = _sample_report()
    report["top_alerts"] = []
    notifier = FeishuNotifier()
    post = notifier.format_daily_report_post(report)
    joined = "\n".join(line[0].get("text", "") for line in post["zh_cn"]["content"] if line)
    assert "今日暂无立即采购提醒" in joined
