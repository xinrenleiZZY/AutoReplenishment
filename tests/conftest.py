"""测试配置"""

import pytest


@pytest.fixture(autouse=True)
def _disable_api_auth(monkeypatch):
    """单测里直接用 TestClient(app) 调后端，不经过前端中间件（中间件负责注入 token）。

    Phase 0 / G-04 启用后 settings.API_AUTH_TOKEN 非空，会让所有 /api/v1 请求 401。
    这里在测试期间把鉴权关掉；鉴权本身的行为由 tests/test_api_auth.py 单独验证。
    """
    from app.config import settings

    monkeypatch.setattr(settings, "API_AUTH_TOKEN", "", raising=False)
    yield


@pytest.fixture
def sample_asin() -> str:
    return "B0XXXXXX"
