"""批量计算调度模块 - 计算编排器（支持按等级频率计算）"""

import asyncio
import calendar
import json
import logging
import os
from datetime import date, timedelta
from math import ceil
from typing import Optional

from sqlalchemy import and_, func, select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory
from app.models.product import Product
from app.models.sales import SalesData
from app.models.daily_sales_stat import DailySalesStat
from app.models.inventory import InventorySnapshot
from app.models.daily_snapshot import DailySalesSnapshot
from app.models.calculation import (
    CalculationResult,
    CalculationStepResult,
    CalculationSkipLog,
    CalculationTimelineReset,
)
from app.models.ai_evaluation import AiEvaluation
from app.models.operator import Operator
from app.services.time_axis import get_sales_phase, check_purchase_window, get_recommended_transport, get_festival_info, resolve_lead_time
from app.services.semantic_classify import buffer_days, get_semantic_classification
from app.services.forecast import (
    forecast_all_months,
    month_lifecycle_ratio,
    compute_trend_coeff,
    compute_ad_coeff,
    compute_listing_coeff,
    compute_market_coeff,
)
from app.services import new_product_policy
from app.services import ai_eval
from app.services.sales_fallback import (
    get_daily_sales_dual,
    sum_daily_sales_dual,
)

"""自动补货决策系统 · 计算任务（C1 拆分后的薄壳）

本文件只做"对外入口聚合"：实现已按域拆到 app/services/calc/。
保持 app.tasks.calculation_tasks.<name> 路径不变（API/脚本/测试均依赖该路径）。
"""

from app.services.calc.common import (  # noqa: F401
    _deep_find_acos,
    _fetch_acos,
    _get_new_product_cfg,
    _parse_step_json,
    _recent_daily_orders,
    _safe_float,
    _safe_int,
    _to_float_safe,
)
from app.services.calc.flow import (  # noqa: F401
    StepRecorder,
    _analyze_sales_trend,
    _latest_calculation_rows,
    _product_ad_profit_metrics,
    _report_score,
    _select_daily_alerts,
    _stockout_risk_reason,
    _to_json,
    get_daily_summary,
    get_due_calculation_stats,
    get_next_calculation_info,
    run_batch_calculation,
    run_due_calculation,
    run_level_calculation,
    run_single_calculation,
)
from app.services.calc.forecast import (  # noqa: F401
    TREND_COEFF_MAX,
    _analyze_sales_history,
    _build_forecast_coefficients,
    _calc_yoy,
    _festival_sales_window,
    _fmt_trend_coeff,
    _forecast_sales,
    _forecast_sales_ai,
    _forecast_sales_festival,
    _forecast_sales_long_term,
    _forecast_sales_new_product,
    _is_new_product,
    _long_term_sales_window,
    _month_end,
    _month_keys_between,
    _prev_month_key,
    _shift_year,
)
from app.services.calc.inventory import (  # noqa: F401
    _analyze_inventory,
    _arbitrate_verdicts,
    _calc_festival_window,
    _calc_festival_window_legacy,
    _check_hard_rules,
    _check_long_term_stock_cover,
    _check_second_peak_replenishment,
    _festival_window_months,
    _identify_lifecycle,
    _normalize_verdict,
    _old_product_inventory_days,
    _run_new_product_flow,
    _window_coverage,
)
from app.services.calc.schedule import (  # noqa: F401
    LEVEL_3TIER,
    _ALL_LEVELS,
    _P_LEVEL_MAP,
    _load_effective_last_dates,
    _load_frequency_config,
    _load_report_lifecycles,
    _load_schedule_overrides,
    _normalize_levels,
    _normalize_lifecycles,
    _parse_frequencies,
    get_calculation_frequency_days,
    get_product_level,
    is_calculation_due,
    normalize_level,
    reset_calculation_timeline,
)
from app.services.calc.score import (  # noqa: F401
    _build_batch_ai_context,
    _calc_score,
    _format_batch_plan,
    _format_score_detail,
    _plan_batches,
    _six_dim_total,
)
from app.services.calc.suggest import (  # noqa: F401
    _base_suggested_gap,
    _calc_purchase_trigger,
    _calc_suggested_qty,
    _calc_suggested_qty_v2,
    _dump_sellable_gap_sim,
    _festival_window_air_catchable,
    _simulate_sellable_before_arrival,
)
