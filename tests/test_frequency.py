"""按等级计算频率的单元测试"""

import pytest

import app.tasks.calculation_tasks as ct
from app.models.product import Product
from datetime import date


@pytest.fixture(autouse=True)
def reset_freq_cache():
    """保证每次测试重新解析频率配置（模块级缓存）"""
    ct._FREQ_CACHE = None
    yield
    ct._FREQ_CACHE = None


def make_product(product_level=None, calc_frequency=None):
    return Product(
        asin="TESTASIN",
        product_name="测试产品",
        product_level=product_level,
        calc_frequency=calc_frequency,
        status=True,
    )


class TestParseFrequencies:
    def test_default_config(self):
        assert ct._parse_frequencies("S:1,A:3,B:5,C:7,D:14") == {
            "S": 1, "A": 3, "B": 5, "C": 7, "D": 14,
        }

    def test_custom_and_blank_parts(self):
        assert ct._parse_frequencies("S:2,B:7,  C:10 ,,") == {"S": 2, "B": 7, "C": 10}


class TestProductLevel:
    def test_from_product_level(self):
        assert ct.get_product_level(make_product(product_level="S")) == "S"

    def test_from_calc_frequency(self):
        assert ct.get_product_level(make_product(calc_frequency="P0")) == "S"
        assert ct.get_product_level(make_product(calc_frequency="P4")) == "D"

    def test_calc_frequency_preferred(self):
        assert ct.get_product_level(make_product(product_level="B", calc_frequency="P4")) == "D"

    def test_unknown(self):
        assert ct.get_product_level(make_product()) == "未知"


class TestFrequencyDays:
    def test_level_based(self):
        assert ct.get_calculation_frequency_days(make_product(product_level="S")) == 1
        assert ct.get_calculation_frequency_days(make_product(product_level="A")) == 3
        assert ct.get_calculation_frequency_days(make_product(product_level="B")) == 5
        assert ct.get_calculation_frequency_days(make_product(product_level="C")) == 7
        assert ct.get_calculation_frequency_days(make_product(product_level="D")) == 14

    def test_p_class_based(self):
        assert ct.get_calculation_frequency_days(make_product(calc_frequency="P0")) == 1
        assert ct.get_calculation_frequency_days(make_product(calc_frequency="P3")) == 7

    def test_numeric_override(self):
        assert ct.get_calculation_frequency_days(make_product(product_level="S", calc_frequency="2")) == 2

    def test_default_for_unknown(self):
        assert ct.get_calculation_frequency_days(make_product()) == 14


class TestIsCalculationDue:
    def test_no_history_is_due(self):
        assert ct.is_calculation_due(None, date(2026, 8, 4), 14) is True

    def test_not_due_within_frequency(self):
        assert ct.is_calculation_due(date(2026, 8, 3), date(2026, 8, 4), 3) is False  # 1天 < 3天
        assert ct.is_calculation_due(date(2026, 8, 2), date(2026, 8, 4), 3) is False  # 2天 < 3天

    def test_due_at_boundary(self):
        assert ct.is_calculation_due(date(2026, 8, 1), date(2026, 8, 4), 3) is True  # 正好3天
        assert ct.is_calculation_due(date(2026, 8, 4), date(2026, 8, 4), 1) is False  # 当天不算
        assert ct.is_calculation_due(date(2026, 8, 3), date(2026, 8, 4), 1) is True  # 超过1天

    def test_due_after_frequency(self):
        assert ct.is_calculation_due(date(2026, 7, 15), date(2026, 8, 4), 14) is True  # 20天 >= 14天
