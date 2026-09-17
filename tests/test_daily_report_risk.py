"""采购日报断货风险提醒逻辑单测（不依赖数据库）"""

from types import SimpleNamespace

from app.tasks.calculation_tasks import (
    _analyze_sales_trend,
    _format_batch_plan,
    _format_score_detail,
    _select_daily_alerts,
    _stockout_risk_reason,
)


def _row(asin: str, days=None, cycle=None, stock=None, level="暂停", score=50):
    return SimpleNamespace(
        asin=asin,
        inventory_days=days,
        replenishment_cycle=cycle,
        available_stock=stock,
        purchase_level=level,
        purchase_score=score,
    )


def test_stockout_risk_reason_cases():
    """断货风险判定：可用库存0 / 库存天数<补货周期 / <危险阈值 / 无风险"""
    assert _stockout_risk_reason(_row("A", days=0, stock=0), 15) == "可用库存为0，已无货可售"
    assert _stockout_risk_reason(_row("B", days=20, cycle=45, stock=100), 15) == \
        "库存仅覆盖20天，低于补货周期45天"
    assert _stockout_risk_reason(_row("C", days=10, cycle=45, stock=100), 15) == \
        "库存仅覆盖10天，低于补货周期45天"
    assert _stockout_risk_reason(_row("E", days=12, cycle=5, stock=100), 15) == \
        "库存仅覆盖12天，低于危险阈值15天"
    assert _stockout_risk_reason(_row("F", days=60, cycle=45, stock=100), 15) is None
    assert _stockout_risk_reason(_row("G", days=None, cycle=45, stock=100), 15) is None


def test_select_daily_alerts_only_risks():
    """无立即采购/观察时，断货风险产品填满提醒位"""
    risks = [_row(f"R{i}", days=i + 1, cycle=45) for i in range(12)]
    rows = _select_daily_alerts([], [], risks)
    assert len(rows) == 10
    assert rows[0].asin == "R0"  # 库存天数最少者优先


def test_select_daily_alerts_risk_reserved():
    """有立即采购/观察时，断货风险至少保留展示位"""
    immediate = [_row("I1", days=3, cycle=45, level="立即采购", score=95),
                 _row("I2", days=4, cycle=45, level="立即采购", score=90)]
    observe = [_row("O1", days=6, cycle=45, level="观察", score=70)]
    risks = [_row(f"R{i}", days=8 + i, cycle=45) for i in range(10)]
    rows = _select_daily_alerts(immediate, observe, risks)
    assert rows[0].asin == "I1"
    assert rows[1].asin == "I2"
    assert rows[2].asin == "O1"
    risk_asins = {r.asin for r in rows if r.asin.startswith("R")}
    assert len(risk_asins) >= 3  # 至少3个断货风险展示位
    assert risk_asins == {"R0", "R1", "R2", "R3", "R4", "R5", "R6"}  # 剩余名额按库存天数补齐
    assert len(rows) == 10


def test_select_daily_alerts_no_risk_keeps_immediate_observe():
    """无断货风险时保持原行为：立即采购/观察按评分降序取前10"""
    immediate = [_row(f"I{i}", days=5, cycle=45, level="立即采购", score=95 - i) for i in range(12)]
    rows = _select_daily_alerts(immediate, [], [])
    assert len(rows) == 10
    assert rows[0].asin == "I0"


def test_format_score_detail_legacy():
    """老品六维评分明细 → 可读文本"""
    detail = {
        "shortage_score": {"value": 100, "weight": 0.25, "label": "断货风险"},
        "trend_score": {"value": 50, "weight": 0.25, "label": "销量趋势"},
        "profit_score": {"value": 60, "weight": 0.20, "label": "利润空间"},
        "life_score": {"value": 70, "weight": 0.10, "label": "生命周期"},
        "urgency": {"value": 80, "weight": 0.10, "label": "库存紧急"},
        "transport_score": {"value": 100, "weight": 0.10, "label": "运输可达"},
    }
    assert _format_score_detail(detail) == \
        "断货风险100·销量趋势50·利润空间60·生命周期70·库存紧急80·运输可达100"


def test_format_score_detail_new_product_reason():
    """新品决策对象 → 取 reason 字段"""
    assert _format_score_detail({"level": "建议采购", "reason": "日均单量超阈值且ACOS达标"}) == \
        "日均单量超阈值且ACOS达标"


def test_format_score_detail_empty():
    assert _format_score_detail(None) == ""
    assert _format_score_detail({}) == ""


def test_format_batch_plan():
    plan = {"batches": [
        {"batch_no": 1, "qty": 500, "method": "空运", "days": 55},
        {"batch_no": 2, "qty": 1200, "method": "海运", "days": 75},
    ], "total_qty": 1700}
    assert _format_batch_plan(plan) == "1.空运500件(55天)；2.海运1200件(75天)"
    assert _format_batch_plan({"batches": [], "total_qty": 0}) == ""
    assert _format_batch_plan(None) == ""


def test_analyze_sales_trend_cases():
    """销量趋势：上升/下降/平稳/无销量/数据不足"""
    rising = _analyze_sales_trend([1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2, 2])
    assert rising["direction"] == "上升"
    assert rising["last7"] == 14 and rising["prev7"] == 7 and rising["change_percent"] == 100
    assert "趋势上升" in rising["text"]

    falling = _analyze_sales_trend([2, 2, 2, 2, 2, 2, 2, 1, 1, 1, 1, 1, 1, 1])
    assert falling["direction"] == "下降"
    assert falling["change_percent"] == -50

    flat = _analyze_sales_trend([5] * 14)
    assert flat["direction"] == "平稳"
    assert flat["change_percent"] == 0

    none_sales = _analyze_sales_trend([0] * 14)
    assert none_sales["direction"] == "无销量"
    assert "近7天0件" in none_sales["text"]

    short = _analyze_sales_trend([3] * 5)
    assert short["last7"] == 15 and short["prev7"] == 0
    assert short["direction"] == "上升"
