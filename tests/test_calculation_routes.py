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
    async def fake_due(progress=None):
        return _fake_stats()

    async def fake_batch(progress=None):
        return _fake_stats()

    monkeypatch.setattr(calc_api, "run_due_calculation", fake_due)
    monkeypatch.setattr(calc_api, "run_batch_calculation", fake_batch)

    with TestClient(app) as client:
        resp = client.post("/api/v1/calculation/trigger/due")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["job_id"]
        assert body["status"] == "running"

        resp = client.post("/api/v1/calculation/trigger/batch")
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"]

        # 单 ASIN 触发仍正常（不应被 /trigger/due 或 /trigger/batch 抢占）
        resp = client.post("/api/v1/calculation/trigger/ANY_ASIN")
        assert resp.status_code == 400
        assert resp.json()["detail"] == "产品不存在"

        # 任务状态可查询
        job = client.get(f"/api/v1/calculation/jobs/{body['job_id']}")
        assert job.status_code == 200
        assert job.json()["status"] in ("running", "done")


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


def test_results_overview_and_config():
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/results/overview?limit=5")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] > 0
        assert len(body["items"]) <= 5
        first = body["items"][0]
        assert {"asin", "product_name", "purchase_score", "purchase_level",
                "suggested_qty", "inventory_days", "purchase_trigger"} <= set(first.keys())

        resp = client.get("/api/v1/config/")
        assert resp.status_code == 200, resp.text
        params = resp.json()
        assert any(p["key"] == "calc_frequencies" for p in params)

        resp = client.put("/api/v1/config/calc_frequencies", json={"value": "S:1,A:3,B:5,C:7,D:14"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["value"] == "S:1,A:3,B:5,C:7,D:14"


def test_products_pagination():
    with TestClient(app) as client:
        resp = client.get("/api/v1/products?limit=20&skip=0")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] > 0
        assert len(body["items"]) <= 20
        assert {"asin", "product_name", "brand", "shop", "msku"} <= set(body["items"][0].keys())

        resp = client.get("/api/v1/products?limit=20&skip=20")
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["items"]) <= 20


def test_analysis_summary_and_level_trigger(monkeypatch):
    with TestClient(app) as client:
        resp = client.get("/api/v1/analysis/summary?level=S")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["level"] == "S"
        assert {"overview", "distributions", "top_sales", "risks", "items"} <= set(body.keys())
        assert body["total"] >= 0

    async def fake_level(level, progress=None):
        return {"total": 0, "due": 0, "skipped": 0, "success": 0, "failed": 0,
                "immediate": 0, "observe": 0, "pause": 0, "by_level": {}, "errors": []}

    monkeypatch.setattr(calc_api, "run_level_calculation", fake_level)
    with TestClient(app) as client:
        resp = client.post("/api/v1/calculation/trigger/level/A")
        assert resp.status_code == 200, resp.text
        assert resp.json()["job_id"]
        job = client.get(f"/api/v1/calculation/jobs/{resp.json()['job_id']}")
        assert job.status_code == 200
        assert job.json()["status"] in ("running", "done")
