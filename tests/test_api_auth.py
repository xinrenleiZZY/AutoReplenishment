"""Phase 0 / G-04：写操作鉴权（X-API-Token）行为测试

覆盖：未带 token → 401；带正确 token → 放行；app-info 免鉴权；
      token 为空（默认关闭）时行为与改造前一致。
"""

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app


def test_api_v1_requires_token_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "test-token", raising=False)
    with TestClient(app) as client:
        # 写端点：无 token → 401
        r = client.post("/api/v1/sync-logs/run", params={"sync_type": "product"})
        assert r.status_code == 401, r.text
        # 读端点同样受保护
        r = client.get("/api/v1/ops/alerts")
        assert r.status_code == 401, r.text
        # 错误的 token → 401
        r = client.get("/api/v1/ops/alerts", headers={"X-API-Token": "wrong"})
        assert r.status_code == 401, r.text
        # 正确 token → 通过鉴权（404/200 都不是 401）
        r = client.get("/api/v1/ops/alerts", headers={"X-API-Token": "test-token"})
        assert r.status_code == 200, r.text


def test_app_info_exempt_from_token(monkeypatch):
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "test-token", raising=False)
    with TestClient(app) as client:
        r = client.get("/api/v1/app-info")
        assert r.status_code == 200, r.text


def test_health_not_protected(monkeypatch):
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "test-token", raising=False)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200, r.text


def test_disabled_when_token_empty(monkeypatch):
    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "", raising=False)
    with TestClient(app) as client:
        r = client.get("/api/v1/ops/alerts")
        assert r.status_code == 200, r.text
