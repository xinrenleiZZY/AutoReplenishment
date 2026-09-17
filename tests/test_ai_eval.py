"""DeepSeek AI 评估服务测试（不发起真实网络请求，验证失败回退）"""

from fastapi.testclient import TestClient

from app.main import app
from app.services import ai_eval
from app.services.config_service import PARAM_DEFS


def test_evaluate_purchase_falls_back(monkeypatch):
    """AI 调用失败时回退规则原因，接口仍返回200"""
    async def boom(*args, **kwargs):
        raise RuntimeError("mock network fail")

    monkeypatch.setattr(ai_eval, "chat_completion", boom)

    with TestClient(app) as client:
        resp = client.post("/api/v1/ai/evaluate/B0CXQ44YFT")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "fallback"
        assert body["reason"]


def test_ai_daily_report_falls_back(monkeypatch):
    """AI 日报生成失败时返回规则日报"""
    async def boom(*args, **kwargs):
        raise RuntimeError("mock network fail")

    monkeypatch.setattr(ai_eval, "chat_completion", boom)

    with TestClient(app) as client:
        resp = client.post("/api/v1/ai/daily-report")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "top_alerts" in body


def test_ai_daily_report_input_includes_stockout(monkeypatch):
    """AI 日报输入包含断货风险数量与明细，总结不能只写'库存状况良好'"""
    import json

    from app.services import ai_eval

    captured = {}

    async def fake_chat(messages, temperature=0.3, max_tokens=2000, json_mode=True):
        captured["messages"] = messages
        return json.dumps({"summary": "今日共监控164个产品，其中5个存在断货风险（如B0RISK0001库存仅覆盖7天），需优先补货。", "alerts": []})

    class FakeSession:
        def __init__(self):
            self.added = []

        def add(self, obj):
            self.added.append(obj)

        async def commit(self):
            pass

    monkeypatch.setattr(ai_eval, "chat_completion", fake_chat)
    summary = {
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
                "operator": "张三",
                "purchase_level": "暂停",
                "alert_type": "断货风险",
                "alert_reason": "库存仅覆盖7天，低于补货周期45天",
                "purchase_score": 55,
                "suggested_qty": 0,
                "inventory_days": 7,
            }
        ],
    }
    enriched = None

    async def run():
        nonlocal enriched
        enriched = await ai_eval.generate_daily_report_ai(summary, FakeSession())

    import asyncio
    asyncio.run(run())

    user_content = captured["messages"][-1]["content"]
    assert '"断货风险数量": 5' in user_content
    assert '"暂停产品中断货风险数量": 4' in user_content
    assert "B0RISK0001" in user_content
    assert enriched["ai_summary"].startswith("今日共监控164个产品")


def test_latest_evaluation_endpoint():
    """最近一次AI评估接口：无评估时返回 none"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/ai/latest/B0ZZZZZZZZZ")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "none"


def test_ai_auto_evaluate_config_param():
    assert "ai_auto_evaluate" in PARAM_DEFS


def test_feishu_post_mentions_operators():
    """飞书日报富文本：只@有飞书UID的负责人"""
    from app.integrations.feishu import FeishuNotifier

    notifier = FeishuNotifier()
    post = notifier.format_daily_report_post({
        "calc_date": "2026-08-05",
        "total_asins": 100,
        "immediate_count": 2,
        "observe_count": 1,
        "pause_count": 97,
        "top_alerts": [
            {"asin": "B0001", "product_name": "A产品", "purchase_level": "立即采购",
             "operators": ["张三", "李四"], "feishu_user_ids": ["ou_zhangsan", None],
             "suggested_qty": 100, "inventory_days": 10, "purchase_trigger": "需要采购"},
            {"asin": "B0002", "product_name": "B产品", "purchase_level": "立即采购",
             "operators": ["王五"], "feishu_user_ids": [],
             "suggested_qty": 50, "inventory_days": 12, "purchase_trigger": "需要采购"},
        ],
    })
    flat = str(post)
    assert "ou_zhangsan" in flat      # 张三有UID → @
    assert "无法@）李四" in flat        # 李四无UID → 仅文字
    assert '"user_id": "all"' not in flat  # 不再@所有人
