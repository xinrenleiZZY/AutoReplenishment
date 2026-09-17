"""预测修正系数单元测试（趋势/广告/Listing/市场）"""

from app.services.forecast import (
    compute_ad_coeff,
    compute_listing_coeff,
    compute_market_coeff,
    compute_trend_coeff,
)


class TestTrendCoeff:
    def test_flat(self):
        assert compute_trend_coeff(70, 140, 300) == 1.0

    def test_growth_clamped(self):
        assert compute_trend_coeff(84, 126, 300) == 1.5

    def test_decline_clamped(self):
        assert compute_trend_coeff(14, 70, 300) == 0.5

    def test_no_data(self):
        assert compute_trend_coeff(0, 0, 0) == 1.0


class TestAdCoeff:
    def test_low_acos_boost(self):
        assert compute_ad_coeff(0.10) == 1.10

    def test_mid_acos(self):
        assert compute_ad_coeff(0.25) == 1.05

    def test_high_acos_penalty(self):
        assert compute_ad_coeff(0.60) == 0.85

    def test_none(self):
        assert compute_ad_coeff(None, None) == 1.0
        assert compute_ad_coeff(None, 0.5) == 1.0


class TestListingCoeff:
    def test_high_rating(self):
        assert compute_listing_coeff(4.8) == 1.04

    def test_low_rating(self):
        assert compute_listing_coeff(3.0) == 0.95

    def test_review_growth_clamped(self):
        assert compute_listing_coeff(4.0, 0.5) == 1.1


class TestMarketCoeff:
    def test_rank_up(self):
        assert compute_market_coeff(25000, 50000) == 1.15

    def test_rank_down_clamped(self):
        assert compute_market_coeff(20000, 10000) == 0.8

    def test_band(self):
        assert compute_market_coeff(500) == 1.1
        assert compute_market_coeff(30000) == 1.0
        assert compute_market_coeff(80000) == 0.9

    def test_none(self):
        assert compute_market_coeff(None) == 1.0
