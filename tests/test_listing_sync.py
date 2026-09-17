"""Listing 同步配置与过滤逻辑测试"""

from app.services.config_service import PARAM_DEFS
from scripts.import_lingxing_data import _date_in_range, _parse_asin_list


def test_listing_config_params_exist():
    for key in (
        "listing_sync_mode",
        "listing_create_start",
        "listing_create_end",
        "listing_exclude_asins",
        "listing_keep_asins",
    ):
        assert key in PARAM_DEFS


def test_parse_asin_list():
    assert _parse_asin_list("b012345678, B0ABCDEFGH\nb012345678") == {"B012345678", "B0ABCDEFGH"}
    assert _parse_asin_list("") == set()


def test_date_in_range():
    assert _date_in_range("2026-07-15", "2026-07-01", "2026-07-31") is True
    assert _date_in_range("2026-06-30", "2026-07-01", "2026-07-31") is False
    assert _date_in_range("2026-08-01", "2026-07-01", "2026-07-31") is False
    assert _date_in_range("", "2026-07-01", "2026-07-31") is True  # 无创建时间不排除
    assert _date_in_range("2026-07-15", "", "") is True
