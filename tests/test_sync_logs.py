"""同步日志手动触发接口 + 产品列表全字段回归测试"""

from fastapi.testclient import TestClient

import app.api.v1.sync_logs as sync_logs_api
from app.main import app


def test_trigger_sync_runs_in_background(monkeypatch):
    async def fake_sync():
        return None

    monkeypatch.setattr(sync_logs_api.sync_tasks, "sync_products", fake_sync)

    with TestClient(app) as client:
        resp = client.post("/api/v1/sync-logs/run", params={"sync_type": "product"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["sync_type"] == "product"

        # 未知类型应报错
        resp = client.post("/api/v1/sync-logs/run", params={"sync_type": "unknown"})
        assert resp.status_code == 400


def test_products_list_returns_full_fields():
    with TestClient(app) as client:
        resp = client.get("/api/v1/products", params={"limit": 3})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "total" in body and "items" in body
        assert body["items"]
        item = body["items"][0]
        for key in ("asin", "msku", "local_sku", "fnsku", "listing_title", "price",
                    "thirty_volume", "afn_fulfillable_quantity", "rank", "stars",
                    "reviews_num", "tags", "supplier_name", "cost_price", "operator"):
            assert key in item, f"缺少字段 {key}"
