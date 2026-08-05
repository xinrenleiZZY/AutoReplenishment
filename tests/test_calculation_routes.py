"""计算路由回归测试：/trigger/batch 与 /trigger/due 不能被 /trigger/{asin} 遮蔽"""

from fastapi.testclient import TestClient

import app.api.v1.calculation as calc_api
from app.main import app


def _fake_stats():
    return {
        "total": 1, "due": 1, "skipped": 0, "success": 1, "failed": 0,
        "immediate": 0, "observe": 0, "pause": 0, "by_level": {}, "errors": [],
    }


def test_trigger_batch_and_due_not_shadowed(monkeypatch):
    async def fake_due():
        return _fake_stats()

    async def fake_batch():
        return _fake_stats()

    monkeypatch.setattr(calc_api, "run_due_calculation", fake_due)
    monkeypatch.setattr(calc_api, "run_batch_calculation", fake_batch)

    with TestClient(app) as client:
        resp = client.post("/api/v1/calculation/trigger/due")
        assert resp.status_code == 200, resp.text
        assert resp.json()["message"] == "按频率计算完成"

        resp = client.post("/api/v1/calculation/trigger/batch")
        assert resp.status_code == 200, resp.text
        assert resp.json()["message"] == "批量计算完成"

        # 单 ASIN 触发仍正常（不应被 /trigger/due 或 /trigger/batch 抢占）
        resp = client.post("/api/v1/calculation/trigger/ANY_ASIN")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "产品不存在"


def test_risks_and_history_endpoints():
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/risks")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert {"stockout", "overstock", "profit"} <= set(body.keys())

        resp = client.get("/api/v1/calculation/results/ANY_ASIN/history")
        assert resp.status_code == 200, resp.text
        assert isinstance(resp.json(), list)


def test_api_health_alias():
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "ok"
