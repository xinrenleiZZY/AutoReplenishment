"""广告/利润联合分析逻辑单测"""

from types import SimpleNamespace

import pytest

from app.services import new_product_policy as np
from app.tasks.calculation_tasks import _product_ad_profit_metrics
from scripts.sync_lx_profit import parse_gross_margin


@pytest.fixture(autouse=True)
def _reset_cfg():
    """重置全局配置（usd_cny_rate/cost_exchange_rate=7.2），避免被计算路由测试覆盖为 6.5 导致断言偏差"""
    np._CFG["usd_cny_rate"] = 7.2
    np._CFG["cost_exchange_rate"] = 7.2
    yield


def _product(**kw):
    defaults = {
        "price": "15.99",
        "cost_price": 20.0,      # cg_price 人民币单件价 → 20/7.2≈2.78美元
        "box_quantity": 50,
        "fba_fee": "4.35",
        "referral_fee": "2.55",
        "shipping": "0.00",
        "seven_spend": 30.0,
        "thirty_spend": 68.23,
        "thirty_amount": 1315.18,
        "acos_30d": None,
        "profit_rate": None,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_parse_gross_margin_from_total_sum():
    """领星毛利报表：优先 total_sum.gross_margin"""
    resp = {
        "code": 0,
        "data": {
            "code": 1,
            "data": {
                "total": 2,
                "total_sum": {"gross_margin": "0.2533", "gross_profit": "6918.95"},
                "list": [{"gross_margin": "0.2510"}, {"gross_margin": "0.3009"}],
            },
        },
    }
    assert parse_gross_margin(resp) == 0.2533


def test_parse_gross_margin_falls_back_to_list_avg():
    """无 total_sum 时用 list 均值兜底"""
    resp = {"data": {"data": {"list": [{"gross_margin": "0.2"}, {"gross_margin": "0.4"}]}}}
    assert parse_gross_margin(resp) == 0.3


def test_parse_gross_margin_invalid():
    assert parse_gross_margin(None) is None
    assert parse_gross_margin({}) is None
    assert parse_gross_margin({"data": {"data": {"total_sum": {"gross_margin": "abc"}}}}) is None


def test_parse_gross_margin_zero_is_no_data():
    """无销量产品返回 0.0000 → 视为无数据，不写库"""
    resp = {"data": {"data": {"total_sum": {"gross_margin": "0.0000", "gross_profit": "0.00"}}}}
    assert parse_gross_margin(resp) is None


def test_parse_gross_margin_negative_kept():
    """负毛利（亏损）保留，用于利润风险识别"""
    resp = {"data": {"data": {"total_sum": {"gross_margin": "-0.1234"}}}}
    assert parse_gross_margin(resp) == -0.1234


def test_product_ad_profit_metrics():
    """广告/利润联合字段：花费、占比、估算毛利率"""
    m = _product_ad_profit_metrics(_product())
    assert m["seven_spend"] == 30.0
    assert m["thirty_spend"] == 68.23
    assert m["thirty_amount"] == 1315.18
    assert m["ad_spend_ratio"] == round(68.23 / 1315.18 * 100, 2)
    assert m["acos_30d"] is None
    assert m["profit_rate"] is None
    assert m["est_profit_rate"] is not None and 0 < m["est_profit_rate"] < 1
    assert m["price"] == 15.99
    assert m["cost_price"] == round(20 / 7.2, 4)  # 人民币 ÷ 汇率
    assert m["cost_price_cny"] == 20.0             # 原始人民币采购报价
    assert m["cost_table"] is not None
    assert set(m["cost_table"]["channels"].keys()) == {"sea", "air", "express"}
    assert m["cost_table"]["channels"]["sea"]["margin"] == m["est_profit_rate"]
    assert "profitable" in m["cost_table"]["channels"]["sea"]


def test_product_ad_profit_metrics_real_values():
    """真实回填值：ACOS/利润率直接带出"""
    m = _product_ad_profit_metrics(_product(acos_30d=0.18, profit_rate=0.2533))
    assert m["acos_30d"] == 0.18
    assert m["profit_rate"] == 0.2533


def test_product_ad_profit_metrics_no_amount():
    """无销售额时不计算广告占比"""
    m = _product_ad_profit_metrics(_product(thirty_amount=0))
    assert m["ad_spend_ratio"] is None
