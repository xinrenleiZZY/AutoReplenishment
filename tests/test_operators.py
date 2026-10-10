"""运营人员管理 + 库存健康接口回归测试"""

import uuid

import pytest

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def _unique_name() -> str:
    return f"测试运营-{uuid.uuid4().hex[:8]}"


def test_operator_crud_and_distinct_names():
    name = _unique_name()
    with TestClient(app) as client:
        resp = client.post("/api/v1/operators/", json={"name": name, "role": "运营", "notes": "回归测试"})
        assert resp.status_code == 201, resp.text
        oid = resp.json()["id"]
        try:
            # 重名应 409
            resp = client.post("/api/v1/operators/", json={"name": name})
            assert resp.status_code == 409

            # 更新
            resp = client.put(f"/api/v1/operators/{oid}", json={"role": "高级运营", "status": False})
            assert resp.status_code == 200, resp.text
            assert resp.json()["role"] == "高级运营"
            assert resp.json()["status"] is False

            # 关键字搜索
            resp = client.get("/api/v1/operators", params={"keyword": name})
            assert resp.status_code == 200
            assert len(resp.json()) == 1

            # 重新启用
            resp = client.put(f"/api/v1/operators/{oid}", json={"status": True})
            assert resp.status_code == 200

            # 候选列表只返回启用人员；配置白名单后，白名单外人员不会出现在候选里
            resp = client.get("/api/v1/operators/distinct-names")
            assert resp.status_code == 200
            whitelist = {n.strip() for n in (settings.OPERATOR_WHITELIST or "").split(",") if n.strip()}
            if whitelist:
                assert name not in resp.json()["names"]
            else:
                assert name in resp.json()["names"]
        finally:
            client.delete(f"/api/v1/operators/{oid}")


def test_operator_whitelist_limits_enabled():
    """白名单模式下，启用人员只能是白名单内负责人"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/operators", params={"status": True, "limit": 200})
        assert resp.status_code == 200
        whitelist = {n.strip() for n in (settings.OPERATOR_WHITELIST or "").split(",") if n.strip()}
        if not whitelist:
            return
        enabled = {op["name"] for op in resp.json()}
        assert enabled, "白名单模式应有启用人员"
        assert enabled <= whitelist


def test_daily_report_operators_whitelist_only():
    """日报重点提醒的负责人：只能是白名单内人员，且为清洗后的姓名"""
    whitelist = {n.strip() for n in (settings.OPERATOR_WHITELIST or "").split(",") if n.strip()}
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/daily-report", params={"include_results": "false"})
        assert resp.status_code == 200, resp.text
        for alert in resp.json().get("top_alerts", []):
            for op in alert.get("operators") or []:
                assert op in whitelist, f"负责人 {op} 不在白名单内"
                assert "%" not in op and not op[-1:].isdigit(), f"负责人 {op} 未清洗"


def test_cost_table_endpoint_with_price_override():
    """成本表接口：三渠道利润 + 竞对售价模拟重算"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/cost-table/B0CXHNHYD9")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "sea" in body["channels"] and "air" in body["channels"] and "express" in body["channels"]
        assert "conclusion" in body and "conclusion_ok" in body
        assert body["channels"]["sea"]["margin"] is not None

        resp2 = client.get("/api/v1/calculation/cost-table/B0CXHNHYD9", params={"price": 100})
        assert resp2.status_code == 200, resp2.text
        assert resp2.json()["price"] == 100.0
        assert resp2.json()["conclusion_ok"] is True


@pytest.mark.integration
def test_overview_operator_filter():
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/results/overview", params={"limit": 5})
        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert items, "应有产品数据"
        assert "operator" in items[0]

        names = client.get("/api/v1/operators/distinct-names").json()["names"]
        assert isinstance(names, list)
        if names:
            resp = client.get("/api/v1/calculation/results/overview", params={"operator": names[0], "limit": 5})
            assert resp.status_code == 200
            for it in resp.json()["items"]:
                assert names[0] in (it["operator"] or "")


def test_operator_sync_from_products_and_product_filter():
    """运营人员从产品负责人自动同步；产品列表支持负责人筛选"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/operators")
        assert resp.status_code == 200

        names = client.get("/api/v1/operators/distinct-names").json()["names"]
        assert names, "产品负责人字段应能解析出运营人员"

        resp = client.get("/api/v1/products", params={"limit": 5})
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert items
        first_operator = next((p["operator"] for p in items if p.get("operator")), None)
        if first_operator:
            name = first_operator.split(",")[0].strip()
            resp = client.get("/api/v1/products", params={"operator": name, "limit": 5})
            assert resp.status_code == 200
            for p in resp.json()["items"]:
                assert name in (p.get("operator") or "")


def test_inventory_health_endpoint():
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/inventory-health", params={"limit": 5})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body and "thresholds" in body
        assert body["thresholds"]["danger"] > 0
        assert body["items"]
        keys = {"asin", "product_name", "operator", "inventory_days", "urgency_level", "purchase_trigger"}
        assert keys <= set(body["items"][0].keys())

        # 紧急程度筛选
        resp = client.get("/api/v1/calculation/inventory-health", params={"urgency": "危险", "limit": 5})
        assert resp.status_code == 200
        for it in resp.json()["items"]:
            assert it["urgency_level"] == "危险"


def test_inventory_threshold_config_params():
    with TestClient(app) as client:
        resp = client.get("/api/v1/config/")
        assert resp.status_code == 200
        keys = [p["key"] for p in resp.json()]
        for key in ("inventory_danger_max_days", "inventory_low_max_days", "inventory_healthy_max_days"):
            assert key in keys
