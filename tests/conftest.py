"""测试配置"""

import pytest


@pytest.fixture
def sample_asin() -> str:
    return "B0XXXXXX"
