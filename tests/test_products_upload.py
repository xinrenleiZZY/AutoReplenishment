"""产品 ASIN 上传比对接口测试"""

from fastapi.testclient import TestClient

from app.main import app
from app.api.v1.products import _merge_exclude_asins


def test_merge_exclude_asins():
    """排除列表合并：去重、保留列表优先级最高"""
    merged = _merge_exclude_asins("B0001,B0002", ["B0002", "B0003", "b0004"], keep_raw="B0004")
    assert merged == ["B0001", "B0002", "B0003"]  # B0004 在保留列表 → 不排除
    assert _merge_exclude_asins("", [], "") == []
    assert _merge_exclude_asins("B0001", ["b0001", "B0001"], "") == ["B0001"]


def test_upload_asins_txt_and_compare():
    with TestClient(app) as client:
        resp = client.get("/api/v1/products", params={"limit": 2})
        items = resp.json()["items"]
        assert items, "应有产品数据"
        known = items[0]["asin"]
        unknown = "B0ZZZZZZZZ"

        content = f"{known}\n{unknown}\n".encode("utf-8")
        resp = client.post(
            "/api/v1/products/upload-asins",
            files={"file": ("asins.txt", content, "text/plain")},
            params={"auto_sync": False},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 2
        assert body["matched"] == 1
        assert body["missing"] == 1
        assert unknown in body["missing_asins"]
        assert body["sync_triggered"] is False

        # 同步后重新比对
        resp = client.post("/api/v1/products/compare-asins", json={"asins": [known, unknown]})
        assert resp.status_code == 200, resp.text
        assert resp.json()["matched"] == 1
        assert resp.json()["missing"] == 1


def test_export_asins_csv():
    """导出 ASIN 列表 CSV：含表头与筛选条件"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/products/export-asins", params={"status": True})
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/csv")
        text = resp.content.decode("utf-8-sig")
        assert text.startswith("ASIN,")
        lines = text.strip().splitlines()
        assert len(lines) >= 2


def test_export_exclude_asins_txt():
    """导出排除列表 TXT：每行一个 ASIN"""
    with TestClient(app) as client:
        resp = client.get("/api/v1/products/export-exclude-asins")
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/plain")
        text = resp.content.decode("utf-8")
        for line in text.strip().splitlines():
            assert len(line) == 10


def test_overview_product_level_and_lifecycle_filters():
    with TestClient(app) as client:
        resp = client.get("/api/v1/calculation/results/overview", params={"product_level": "D", "limit": 3})
        assert resp.status_code == 200, resp.text
        for item in resp.json()["items"]:
            assert item["product_level"] == "D"
