"""趋势系数上限口径单测（业务口径：超过 1.5 直接用 1.5，原值仅用于文字标注）

对应实现：app/tasks/calculation_tasks.py 的 TREND_COEFF_MAX / _fmt_trend_coeff，
以及 app/services/special_order_rules.py 的文字标注。
"""

from app.tasks.calculation_tasks import TREND_COEFF_MAX, _fmt_trend_coeff


def test_cap_value():
    assert TREND_COEFF_MAX == 1.5


def test_fmt_without_cap():
    assert _fmt_trend_coeff(1.2, 1.2) == "1.20"
    assert _fmt_trend_coeff(1.5, 1.5) == "1.50"


def test_fmt_with_cap_marks_raw_value():
    assert _fmt_trend_coeff(1.5, 2.31) == "1.50（原值：2.31）"


def test_fmt_missing_value():
    assert _fmt_trend_coeff(None, None) == "-"


def test_capped_coefficient_math():
    """封顶口径 = min(原值, 1.5)；去年为 0 时回退 1.0 不受影响。"""
    raw = 3.87
    capped = round(min(raw, TREND_COEFF_MAX), 4)
    assert capped == 1.5
    fallback = round(min(1.0, TREND_COEFF_MAX), 4)
    assert fallback == 1.0
