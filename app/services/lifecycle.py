"""产品生命周期识别模块

功能：
1. get_base_daily_sales - 基准销量（最近 N 天平均日销量，双源优先）
2. new_product_annual_forecast - 新品年预测销量
3. _map_sales_to_level - 年销量映射产品等级（S/A/B/C/D）
"""

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.sales_fallback import sum_daily_sales_dual

# 生命周期常量
LIFECYCLE_LAUNCH = "启动期"       # 新品默认
LIFECYCLE_GROWTH = "增长期"       # 快速增长
LIFECYCLE_HOT = "热卖期"          # 热卖中
LIFECYCLE_MATURE = "成熟期"       # 稳定成熟
LIFECYCLE_DECLINE = "下降期"      # 销量下降
LIFECYCLE_CLEARANCE = "清库存期"  # 清库存

# 产品等级阈值（年销量）
LEVEL_S_THRESHOLD = 5000
LEVEL_A_THRESHOLD = 2000
LEVEL_B_THRESHOLD = 1000
LEVEL_C_THRESHOLD = 300
LEVEL_D_THRESHOLD = 1

# 等级 → 计算频率（P0最频繁，P4最低频）
LEVEL_FREQUENCY = {
    "S": "P0",
    "A": "P1",
    "B": "P2",
    "C": "P3",
    "D": "P4",
}

# ── 新品年预测（等级评定）：各生命周期预测销量系数 × 安全系数 ──
# 预测销量公式（需求文档）：启动期=基准×1.03^(天数÷10) / 增长期=×1.15 / 热卖期=×1.30 / 成熟期=×1.00 / 下降期=×0.70
# 安全系数：启动期1.0 / 增长期1.15 / 热卖期1.30 / 成熟期1.20 / 下降期1.0
# 每个阶段 tuple: (预测销量系数, 安全系数)；启动期按 预测系数^(d/10) 逐日累加。
# 可通过 config_service 的 lifecycle_coeff（JSON）覆盖。
DEFAULT_LIFECYCLE_COEFF: dict[str, tuple] = {
    "启动期": (1.03, 1.0),
    "增长期": (1.15, 1.15),
    "热卖期": (1.30, 1.30),
    "成熟期": (1.00, 1.20),
    "下降期": (0.70, 1.0),
}
LIFECYCLE_STAGES_FORECAST = ["启动期", "增长期", "热卖期", "成熟期", "下降期"]


async def get_base_daily_sales(session: AsyncSession, asin: str, days: int = 3) -> float | None:
    """基准销量 = 最近 N 天平均日销量（daily_sales_stats 逐日实抓优先，缺时回退 sales_data）

    近 N 天两源均无销量记录 → 返回 None（无基准，新品等级空着，不计算）。
    """
    today = date.today()
    start = today - timedelta(days=days)
    end = today - timedelta(days=1)
    # 双源优先：daily_sales_stats（逐日实抓完整数据）优先，无记录才回退 sales_data
    total = await sum_daily_sales_dual(asin, start, end, session)
    if total is None or total <= 0:
        return None
    return round(total / days, 2)


def new_product_annual_forecast(
    base_daily_sales: float | None,
    stage_days: dict[str, int] | None,
    coeffs: dict[str, tuple] | None = None,
) -> int | None:
    """新品年预测销量 = 基准(近3天日均) × Σ(各阶段预测销量系数 × 天数 × 安全系数)

    仅计算实际生命周期的时间区间阶段天数（缓存表 festival_lifecycle_days）；
    无有效阶段天数（如非节日新品 / 无基准销量）→ 返回 None（等级空着，不计算）。
    coeffs: 可选覆盖 {阶段: (预测系数, 安全系数)}，缺省用 DEFAULT_LIFECYCLE_COEFF。
    """
    if base_daily_sales is None or base_daily_sales <= 0:
        return None
    if not stage_days:
        return None
    coeffs = coeffs or DEFAULT_LIFECYCLE_COEFF
    total = 0.0
    for stage in LIFECYCLE_STAGES_FORECAST:
        n = int(stage_days.get(stage, 0) or 0)
        if n <= 0:
            continue
        predict, safety = coeffs.get(stage, (1.0, 1.0))
        if stage == "启动期":  # 逐日 预测系数^(d/10)
            stage_mult = sum(predict ** (d / 10.0) for d in range(1, n + 1))
        else:
            stage_mult = predict * n
        total += stage_mult * safety
    if total <= 0:
        return None
    return round(base_daily_sales * total)


def _map_sales_to_level(total_sales: int) -> str:
    """根据年销量映射产品等级"""
    if total_sales >= LEVEL_S_THRESHOLD:
        return "S"
    elif total_sales >= LEVEL_A_THRESHOLD:
        return "A"
    elif total_sales >= LEVEL_B_THRESHOLD:
        return "B"
    elif total_sales >= LEVEL_C_THRESHOLD:
        return "C"
    elif total_sales >= LEVEL_D_THRESHOLD:
        return "D"
    return "D"
