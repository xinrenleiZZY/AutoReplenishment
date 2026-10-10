"""新品补货策略（需求文档第六章）单元测试"""

from datetime import date

import pytest

from app.services import new_product_policy as np


@pytest.fixture(autouse=True)
def _reset_cfg():
    """重置全局配置：计算路由等测试会把 usd_cny_rate/cost_exchange_rate 覆盖为
    运行时值(6.5)，本套件断言基于默认 7.2，测试间隔离。"""
    np._CFG["usd_cny_rate"] = 7.2
    np._CFG["cost_exchange_rate"] = 7.2
    yield


class _P:
    price = "30"
    cost_price = "19.1450"
    box_quantity = 60
    fba_fee = "5.91"
    referral_fee = "2.55"
    acos_30d = None
    festival = None


def test_unit_cost_converts_cny_to_usd():
    """cg_price 为人民币单件价，按 USD_CNY_RATE(7.2) 折算美元"""
    assert np.unit_cost(_P()) == round(19.1450 / 7.2, 4)


def test_check_trigger():
    assert np.check_trigger([6, 7, 8]) is True
    assert np.check_trigger([1, 1, 1]) is False
    assert np.check_trigger([2]) is False


def test_check_acos():
    ok, _ = np.check_acos(0.5)
    assert ok
    ok, reason = np.check_acos(0.6)
    assert not ok and "55%" in reason
    ok, _ = np.check_acos(None)
    assert ok  # 无 ACOS 数据按达标放行，避免有销量的新品被误杀


@pytest.mark.integration
def test_calc_cost_table():
    table = np.calc_cost_table(_P())
    assert table["price"] == 30.0
    assert table["unit_cost"] == round(19.1450 / 7.2, 4)
    assert table["all_profitable"] is True
    assert "sea" in table["profitable_modes"]
    assert table["is_peak"] in (True, False)
    assert table["channels"]["sea"]["margin"] is not None
    assert 0 < table["channels"]["sea"]["margin"] < 1
    # 利润 = 售价 − MC(采购成本+运费美元) − P卡费 − 分拣费 − 佣金 − 入库配置费
    #       − 仓储费 − 广告费(17%) − 退货平摊(3%) − 超阈值亏损 − 附加费(3.5%×分拣)
    # 运费：按件计费，旺季15/淡季17（¥/件 ÷ 汇率）
    ch = table["channels"]["sea"]
    sea_fee_cny = 15 if table["is_peak"] else 17
    expect_mc = round(round(19.1450 / 7.2, 4) + round(sea_fee_cny / 7.2, 4), 4)
    expect_profit = round(
        30 - expect_mc - round((30 - 5.91 - 2.55) * 0.03, 4) - 5.91 - 2.55
        - 0.32 - 0.1093 - round(30 * 0.17, 4) - round(30 * 0.03, 4)
        - 0.0 - round(5.91 * 0.035, 4), 4
    )
    assert abs(ch["profit"] - expect_profit) < 0.001


def test_calc_cost_table_price_override():
    """竞对售价模拟：填高价后利润提升，渠道盈利状态随之变化"""
    table = np.calc_cost_table(_P(), price_override=35)
    assert table["price"] == 35.0
    assert table["channels"]["sea"]["profit"] > np.calc_cost_table(_P(), price_override=30)["channels"]["sea"]["profit"]


def test_check_cost_table_near_one_dollar_warning():
    """盈利但最高Profit不足1美金 → 提示利润偏薄需谨慎"""
    p = _P()
    p.price = "20"  # 压低售价使利润接近0~1美金
    table = np.calc_cost_table(p)
    if table["all_profitable"]:
        ok, reason = np.check_cost_table(table)
        assert ok is True
        if max(c["profit"] for c in table["channels"].values()) < 1.0:
            assert "不足1美金" in reason


def test_decision_not_triggered():
    d = np.new_product_decision(_P(), [1, 1, 1], 0.4, 300, 12, "热卖期", None, False, 20)
    assert d["level"] == "未触发"
    assert d["suggested_qty"] == 0


def test_decision_acos_fail():
    d = np.new_product_decision(_P(), [6, 7, 8], 0.6, 300, 12, "热卖期", None, False, 20)
    assert d["level"] == "终止"
    assert "ACOS" in d["reason"]


def test_decision_cost_fail():
    p = _P()
    p.price = "10"
    d = np.new_product_decision(p, [6, 7, 8], 0.4, 300, 12, "热卖期", None, False, 20)
    assert d["level"] == "终止"
    assert "Profit" in d["reason"]


def test_decision_long_term():
    d = np.new_product_decision(_P(), [6, 7, 8], 0.4, 300, 12, "热卖期", None, False, 20)
    assert d["level"] == "建议采购"
    assert d["suggested_qty"] == 720
    assert d["plan"]["final_qty"] == 720


@pytest.mark.integration
def test_decision_festival():
    d = np.new_product_decision(_P(), [6, 7, 8], 0.4, 200, 12, "热卖期", date(2026, 10, 31), False, 20)
    assert d["level"] == "建议采购"
    assert d["suggested_qty"] > 0
    assert d["remaining_days"] is not None and d["remaining_days"] >= 14
    assert len(d["steps"]) >= 5


@pytest.mark.integration
def test_run_new_product_flow_branch(monkeypatch):
    """新品决策分支编排：门禁通过后输出建议采购数量与批次"""
    import asyncio
    from datetime import date

    import app.tasks.calculation_tasks as ct
    from app.models.product import Product

    class _Recorder:
        def __init__(self):
            self.steps = []

        def record(self, step_no, step_name, output, reason="", status="success", input_data=None):
            self.steps.append({"step_no": step_no, "step_name": step_name, "status": status})

    async def fake_cfg(session):
        return {
            "trigger_days": 3, "trigger_min_order": 5.0, "acos_max": 0.55,
            "min_selling_days": 14, "decoration_buffer_days": 14, "non_decoration_buffer_days": 3,
            "long_term_safety_factor": 1.2,
            "transport_modes": {
                "sea": {"label": "海运", "slow_days": 30, "peak_days": 45, "slow_fee": 15.0, "peak_fee": 17.0},
                "air": {"label": "空派", "slow_days": 10, "peak_days": 15, "slow_fee": 60.0, "peak_fee": 70.0},
                "express": {"label": "快递", "slow_days": 3, "peak_days": 6, "slow_fee": 70.0, "peak_fee": 80.0},
            },
        }

    async def fake_acos(product):
        return 0.4

    async def fake_orders(asin, session, days=7):
        return [6, 7, 8]

    async def fake_festival(festival, session):
        return {}

    monkeypatch.setattr(ct, "_get_new_product_cfg", fake_cfg)
    monkeypatch.setattr(ct, "_fetch_acos", fake_acos)
    monkeypatch.setattr(ct, "_recent_daily_orders", fake_orders)
    monkeypatch.setattr(ct, "get_festival_info", fake_festival)

    product = Product(
        asin="B0TESTNEW", product_name="测试新品", status=True, product_stage="新品",
        price="30", cost_price="19.1450", box_quantity=60, fba_fee="5.91", referral_fee="2.55",
    )
    recorder = _Recorder()
    result = asyncio.run(ct._run_new_product_flow(
        "B0TESTNEW", product, None, recorder, date(2026, 8, 5),
        {"available_stock": 300}, "热卖期", 20,
    ))
    assert result["trigger"]["purchase_trigger"] == "需要采购"
    assert result["suggested_qty"] == 420  # 日均7单 × 补货周期50天 × 安全系数1.2 = 420
    names = [s["step_name"] for s in recorder.steps]
    assert "新品触发条件" in names
    assert "建议采购数量" in names
