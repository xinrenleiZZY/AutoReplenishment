"""DeepSeek AI 评估服务（直接调用官方 API，OpenAI 兼容格式）

用途：
  - evaluate_purchase: 对单个ASIN给出采购决策综合评估（未来销量/建议数量/原因/风险）
  - generate_daily_report_ai: 生成采购日报整体总结 + 每条重点提醒的全面分析

设计原则：
  - AI 只做“解释/校验/建议”，规则计算结果始终兜底
  - 失败/超时/未配置时回退规则原因，不影响主流程
  - 每次评估落库 ai_evaluations，可追溯
"""

import json
import logging
import time
from datetime import date, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.ai_evaluation import AiEvaluation
from app.models.calculation import CalculationResult, CalculationStepResult
from app.models.inventory import InventorySnapshot
from app.models.product import Product
from app.models.sales import SalesData
from app.models.sales_statistics import SalesStatisticsReport
from app.services import new_product_policy
from app.services.sales_fallback import (
    get_daily_sales_dual,
    sum_daily_sales_dual,
)

logger = logging.getLogger(__name__)


def ai_enabled() -> bool:
    return bool(settings.DEEPSEEK_AI_EVAL_ENABLED and settings.DEEPSEEK_API_KEY)


async def chat_completion(
    messages: list,
    temperature: float = 0.3,
    max_tokens: int = 2000,
    json_mode: bool = True,
    timeout: float | None = None,
) -> str:
    """调用 DeepSeek Chat Completions（OpenAI 兼容，支持 JSON mode）"""
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("未配置 DEEPSEEK_API_KEY")
    url = f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=timeout or settings.DEEPSEEK_TIMEOUT) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    choice = data["choices"][0]
    content = (choice["message"].get("content") or "").strip()
    if not content:
        raise RuntimeError(
            f"AI 返回空正文：finish_reason={choice.get('finish_reason')}，usage={data.get('usage')}"
        )
    return content


def _parse_json_text(text: str) -> dict:
    """解析模型输出（容忍 markdown 代码块）"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


async def _save_evaluation(
    session: AsyncSession,
    asin: str,
    eval_type: str,
    input_data: dict,
    output_data: dict,
    model: str,
    status: str,
    latency_ms: int,
    error_message: str | None = None,
):
    session.add(AiEvaluation(
        asin=asin,
        calc_date=date.today(),
        eval_type=eval_type,
        input_data=json.dumps(input_data, ensure_ascii=False, default=str),
        output_data=json.dumps(output_data, ensure_ascii=False, default=str),
        model=model,
        status=status,
        latency_ms=latency_ms,
        error_message=error_message,
    ))
    await session.commit()


def _fallback_reason(result, cost_table=None) -> str:
    """规则兜底原因"""
    trigger = result.purchase_trigger or ""
    qty = result.suggested_qty or 0
    days = result.inventory_days
    cycle = result.replenishment_cycle
    parts = []
    if trigger:
        parts.append(f"触发判断：{trigger}")
    if days is not None and cycle is not None:
        parts.append(f"库存可售{days}天 vs 补货周期{cycle}天")
    if qty > 0:
        parts.append(f"建议采购{qty}件")
    if cost_table and cost_table.get("channels"):
        profits = {m: c["profit"] for m, c in cost_table["channels"].items()}
        parts.append(f"成本表利润：{profits}")
    return "；".join(parts) if parts else "无历史数据"


async def _fetch_last_year_30d(asin: str, session: AsyncSession, start: date, end: date):
    """去年近30天销量：匹配"近30天日销量"窗口的去年同日区间。

    优先读 sales_statistics_reports 缓存（领星 sales-statistics 按日区间实抓落库）；
    缓存缺失时调用领星网页API（filterDateType=day）实抓并幂等落库，再读取。
    返回 int（销量）或 None。
    """
    _row = (await session.execute(
        select(SalesStatisticsReport).where(
            SalesStatisticsReport.asin == asin,
            SalesStatisticsReport.stat_start_date == start.isoformat(),
            SalesStatisticsReport.stat_end_date == end.isoformat(),
        )
    )).scalar_one_or_none()
    if _row is not None and _row.total_value is not None:
        return int(float(_row.total_value))

    # 该窗口若已有任意记录（说明已同步过），本ASIN无记录即去年该窗口无销量，不重复同步
    _window_synced = (await session.execute(
        select(SalesStatisticsReport.id).where(
            SalesStatisticsReport.stat_start_date == start.isoformat(),
            SalesStatisticsReport.stat_end_date == end.isoformat(),
        ).limit(1)
    )).scalar_one_or_none()
    if _window_synced is not None:
        return None

    # 无缓存 → 调领星 sales-statistics 网页API 按日区间实抓并落库（幂等：同区间先删后写）
    try:
        from scripts.sync_sales_statistics import sync_sales_statistics as _sync

        await _sync(start=start.isoformat(), end=end.isoformat(),
                    query_type="volume", group_type="asin", filter_date_type="day")
        _row = (await session.execute(
            select(SalesStatisticsReport).where(
                SalesStatisticsReport.asin == asin,
                SalesStatisticsReport.stat_start_date == start.isoformat(),
                SalesStatisticsReport.stat_end_date == end.isoformat(),
            )
        )).scalar_one_or_none()
        if _row is not None and _row.total_value is not None:
            return int(float(_row.total_value))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{asin}] 去年近30天({start}~{end})抓取失败: {e}")
    return None


async def evaluate_purchase(asin: str, session: AsyncSession) -> dict:
    """对单个 ASIN 做 DeepSeek 采购决策评估"""
    started = time.time()
    product = (await session.execute(
        select(Product).where(Product.asin == asin)
    )).scalar_one_or_none()
    if product is None:
        return {"asin": asin, "status": "failed", "error": "产品不存在"}

    latest = (await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    history_rows = (await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin, CalculationResult.calc_date >= date.today() - timedelta(days=30))
        .order_by(CalculationResult.calc_date)
    )).scalars().all()
    history = [
        {
            "日期": r.calc_date.isoformat(),
            "级别": r.purchase_level,
            "建议数量": r.suggested_qty,
            "库存天数": r.inventory_days,
            "触发": r.purchase_trigger,
        }
        for r in history_rows[-14:]
    ]

    # 前三次分析结果 + 人工反馈（供 AI 评估预测时参考历史表现与纠偏意见）
    recent3 = (await session.execute(
        select(CalculationResult)
        .where(CalculationResult.asin == asin)
        .order_by(CalculationResult.calc_date.desc())
        .limit(3)
    )).scalars().all()
    last_three = []
    for r in reversed(recent3):  # 按日期升序展示
        score_detail = None
        if r.score_detail:
            try:
                score_detail = json.loads(r.score_detail)
            except (json.JSONDecodeError, TypeError):
                score_detail = r.score_detail
        last_three.append({
            "日期": r.calc_date.isoformat(),
            "预测总销量": r.forecast_total,
            "可用库存": r.available_stock,
            "库存天数": r.inventory_days,
            "触发": r.purchase_trigger,
            "建议数量": r.suggested_qty,
            "评分": r.purchase_score,
            "级别": r.purchase_level,
            "评分明细": score_detail,
            "人工反馈": r.user_feedback,
            "反馈时间": r.feedback_at.isoformat() if r.feedback_at else None,
        })

    # 公式预测系数（Step5 未来销量预测落库：趋势/市场/广告/Listing）
    formula_coeffs = None
    step5 = (await session.execute(
        select(CalculationStepResult)
        .where(CalculationStepResult.asin == asin, CalculationStepResult.step_no == 5)
        .order_by(CalculationStepResult.calc_date.desc(), CalculationStepResult.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if step5 and step5.output_data:
        try:
            step5_out = json.loads(step5.output_data)
            formula_coeffs = step5_out.get("forecast_coeffs")
        except (json.JSONDecodeError, TypeError):
            formula_coeffs = None

    cost_table = new_product_policy.calc_cost_table(product)

    # 近30天日销量（未来销量评估基础）
    _start30 = date.today() - timedelta(days=30)
    sales_rows = (await session.execute(
        select(SalesData.date, SalesData.sales_qty)
        .where(SalesData.asin == asin, SalesData.date >= _start30)
        .order_by(SalesData.date)
    )).all()
    # 双源优先：daily_sales_stats（逐日实抓）优先，缺时回退 sales_data
    if not sales_rows:
        _fb = await get_daily_sales_dual(asin, _start30, date.today(), session, use_zero=True)
        sales_rows = [(d, q) for d, q in sorted(_fb.items())]
        logger.info(f"[{asin}] 近30天销量双源优先 daily_sales_stats: {len(sales_rows)} 条")
    daily_sales = [{"日期": r[0].isoformat(), "销量": r[1]} for r in sales_rows]
    sales_total = sum(r[1] for r in sales_rows)
    sales_avg = round(sales_total / max(len(sales_rows), 1), 2)
    last7 = sum(r[1] for r in sales_rows[-7:])
    prev7 = sum(r[1] for r in sales_rows[-14:-7])
    trend = "上升" if last7 > prev7 * 1.1 else ("下降" if last7 < prev7 * 0.9 else "平稳")
    # 老品趋势：用系统同比涨跌%（该月 vs 去年同月）数值化；新品保留近7天 vs 前7天文字趋势
    if getattr(product, "product_stage", None) == "老品":
        trend = "数据不足"  # 无去年同月数据时占位，_yoy 计算后再覆盖

    # 库存快照（可用库存组成）
    snap = (await session.execute(
        select(InventorySnapshot)
        .where(InventorySnapshot.asin == asin)
        .order_by(InventorySnapshot.snapshot_date.desc())
        .limit(1)
    )).scalar_one_or_none()

    # 同比/环比：历史月度表（领星利润报表按月回填 + SIF 趋势）
    from app.models.historical_monthly import HistoricalMonthlyStats

    hist_rows = (await session.execute(
        select(HistoricalMonthlyStats).where(HistoricalMonthlyStats.asin == asin)
    )).scalars().all()
    hist = {(r.month, r.source): r for r in hist_rows}
    _today = date.today()
    _lm = _today.month - 1 or 12
    _ly = _today.year - (1 if _today.month == 1 else 0)
    _lm_key = f"{_ly}-{_lm:02d}"                     # 上月
    _lpm_key = f"{_today.year - 1}-{_lm:02d}"        # 去年上月（同比基准，完整月）
    _lpm = hist.get((_lpm_key, "lingxing"))
    _last_m = hist.get((_lm_key, "lingxing"))
    _yoy = None
    if _last_m and _lpm and (_lpm.sale_quantity or 0) > 0:
        _yoy = round(((_last_m.sale_quantity or 0) - _lpm.sale_quantity) / _lpm.sale_quantity * 100, 1)
    # 老品趋势：用同比涨跌%（该月 vs 去年同月）数值化，覆盖初始占位
    if getattr(product, "product_stage", None) == "老品":
        if _yoy is None:
            trend = "数据不足"
        elif _yoy > 0:
            trend = f"上升(+{_yoy}%)"
        elif _yoy < 0:
            trend = f"下降({_yoy}%)"
        else:
            trend = "平稳(0%)"

    # 去年近30天：与"近30天日销量"窗口对应的去年同日区间（领星 sales-statistics 按日区间实抓）
    if sales_rows:
        _cur_start, _cur_end = sales_rows[0][0], sales_rows[-1][0]
    else:
        _cur_start, _cur_end = _today - timedelta(days=29), _today
    _ly30_start = _cur_start.replace(year=_cur_start.year - 1)
    _ly30_end = _cur_end.replace(year=_cur_end.year - 1)
    _ly30_value = await _fetch_last_year_30d(asin, session, _ly30_start, _ly30_end)
    # 双源优先：daily_sales_stats（逐日实抓）读去年近30天销量，缺时回退原数据源
    if _ly30_value is None or _ly30_value == 0:
        _fb_ly30 = await sum_daily_sales_dual(asin, _ly30_start, _ly30_end, session)
        if _fb_ly30:
            _ly30_value = _fb_ly30
            logger.info(f"[{asin}] 去年近30天({_ly30_start}~{_ly30_end})兜底 daily_sales_stats: {_fb_ly30}")

    # ── 节日窗口口径（AI 判断节日产品库存覆盖的正确基准） ──
    festival_info = None
    if getattr(product, "festival", None):
        try:
            from app.tasks.calculation_tasks import _analyze_inventory, _calc_festival_window
            _inv = await _analyze_inventory(asin, session)
            _fw = await _calc_festival_window(product, session, _inv)
            if _fw:
                _ws = _fw.get("win_start")
                _we = _fw.get("win_end")
                # 与 _calc_festival_window 口径一致：窗口天数 = 今年窗口（明天→节日结束）的日历天数
                _wd = int(_fw.get("window_days") or 0)
                if _wd <= 0:
                    _wd = ((_we - _ws).days + 1) if (_ws and _we) else None
                _avail = int(_inv.get("available_stock") or 0)
                _est = _fw.get("window_estimate") or 0
                _daily = (_est / _wd) if (_wd and _est > 0) else 0
                # 库存覆盖天数：优先用「预估总量 × 各月占比 → 每月 ÷ 该月天数」逐月扣减结果
                _cov = _fw.get("coverage_days")
                if _cov is None:
                    _cov = round(_avail / _daily) if _daily > 0 else None
                festival_info = {
                    "节日": product.festival,
                    "窗口月份": _fw.get("window_months"),
                    "窗口预估总需求": _est,
                    "窗口内已售": _fw.get("sold_in_window"),
                    "剩余缺口": _fw.get("remaining"),
                    "窗口起始": _ws.isoformat() if _ws else None,
                    "窗口结束": _we.isoformat() if _we else None,
                    "窗口天数": _wd,
                    "窗口日均需求": round(_daily, 1) if _daily else None,
                    "当前可用库存": _avail,
                    "窗口口径库存覆盖天数": _cov,
                    "库存覆盖截止日": _fw.get("coverage_until"),
                    "库存覆盖逐月明细": _fw.get("coverage_months"),
                    "系统最新计算库存天数": latest.inventory_days if latest else None,
                }
        except Exception as _e:
            logger.warning(f"[{asin}] 节日窗口口径数据获取失败: {_e}")

    input_data = {
        "asin": asin,
        "产品名": product.product_name,
        "生命周期": product.life_cycle,
        "产品阶段": product.product_stage,
        "产品类型": product.product_type,
        "节日": product.festival,
        "核心销售月份": product.core_months,
        "季节占比": None,  # 季节曲线已停用
        "售价": product.price,
        "采购单价": new_product_policy.unit_cost(product),
        "单箱数量": product.box_quantity,
        "最低采购量": product.min_order_qty,
        "广告花费(7/30天)": [product.seven_spend, product.thirty_spend],
        "近30天日销量": daily_sales,
        "销量汇总": {"总销量": sales_total, "日均": sales_avg, "近7天": last7, "前7天": prev7, "趋势": trend},
        "同比环比": {
            "去年近30天": {
                "区间": f"{_ly30_start.isoformat()} ~ {_ly30_end.isoformat()}",
                "销量": _ly30_value,
                "数据来源": "领星(sales-statistics)按日区间实抓",
            },
            "上月": {"月份": _lm_key, "销量": _last_m.sale_quantity if _last_m else None},
            "同比涨跌%": _yoy,
        },
        "库存快照": {
            "FBA可售": snap.fba_available if snap else None,
            "FBA预留": snap.fba_reserved if snap else None,
            "FBA在途": snap.fba_inbound if snap else None,
            "本地库存": snap.local_stock if snap else None,
            "采购待到货": snap.purchase_on_order if snap else None,
            "快照日期": snap.snapshot_date.isoformat() if snap else None,
        },
        "领星库存口径": {
            "FBA可售天数": snap.fba_available_days if snap else None,
            "预计售罄日": snap.stockout_date.isoformat() if snap and snap.stockout_date else None,
            "预估日销": snap.estimated_daily_sales if snap else None,
        },
        "成本表": cost_table,
        "最新计算": {
            "日期": latest.calc_date.isoformat() if latest else None,
            "预测总销量": latest.forecast_total if latest else None,
            "预测明细": json.loads(latest.forecast_months) if latest and latest.forecast_months else None,
            "可用库存": latest.available_stock if latest else None,
            "库存天数": latest.inventory_days if latest else None,
            "补货周期": latest.replenishment_cycle if latest else None,
            "触发": latest.purchase_trigger if latest else None,
            "建议数量": latest.suggested_qty if latest else None,
            "级别": latest.purchase_level if latest else None,
            "评分明细": latest.score_detail if latest else None,
            "人工反馈": latest.user_feedback if latest else None,
        },
        "公式预测系数(趋势/市场/广告/Listing)": formula_coeffs or "无落库记录",
        "前三次分析结果与反馈": last_three,
        "近30天历史": history,
        "节日窗口口径": festival_info or None,
    }

    system = (
        "你是亚马逊跨境电商资深补货顾问。基于系统给出的结构化数据（产品档案、成本表、"
        "最新计算结果、近30天日销量、库存快照、季节占比、公式预测系数、前三次分析结果与反馈、近30天历史），"
        "先结合【公式预测系数（趋势/市场/广告/Listing）+ 前三次分析结果与人工反馈 + 销量趋势】评估未来销量预测是否合理，"
        "再对采购决策做全面评估。"
        "重要要求：必须综合【销量趋势、库存健康、成本利润、广告、生命周期、节日/季节窗口、前三次分析反馈、上次补货效果】"
        "多个维度分析，不得只凭单一指标下结论；各因素相互矛盾时，要说明权衡逻辑。"
        "若产品类型为「长期产品」（全年销售不过季），节日/季节窗口维度不适用，"
        "禁止用旺季/淡季/过季等季节理由评估该产品，应按全年稳定需求判断。"
        "【禁止项】任何字段与文本中都严禁出现「生命周期系数」字样及其数值"
        "（如「生命周期:热卖期老品 生命周期系数1.3,」）：系统仅在预测新品销量时内部使用该系数，"
        "老品明确不叠加、整套规则中也无该系数参与，「生命周期」维度只允许描述产品当前所处的阶段"
        "（启动期/增长期/热卖期/成熟期/下降期）及其对需求的定性影响，"
        "不得写出任何系数数字，也不得声称对老品（或对本品）叠加了生命周期系数。"
        "若前三次分析中有运营人员的人工反馈（如纠偏、实际断货、市场变化），必须重点参考并说明采纳情况。"
        "建议数量（suggested_qty）必须精确计算：结合【库存可售天数、可用库存、补货周期、未来预测需求、利润率】给出补货量，"
        "目标为补货后覆盖到补货周期+安全库存；库存覆盖天数充足或处于下降期/利润为负时少补或不补（可返回0）；"
        "不要随手给整数300，数量要与销量/库存数据匹配。"
        "请输出 JSON，格式："
        '{"forecast":{"assessment":"对规则预测的评估(80字内，结合公式系数、前三次分析反馈、近30天销量、趋势、季节占比、广告、去年同月)",'
        '"suggested_forecast_total":整数,"trend":"上升|平稳|下降"},'
        '"factors":{"销量趋势":"...","库存健康":"...","利润/成本":"...","广告ACOS":"...",'
        '"生命周期":"仅描述阶段(启动/增长/热卖/成熟/下降)及需求影响，禁止出现任何系数",'
        '"节日/季节窗口":"...","前三次分析/反馈":"...","上次补货效果":"..."},'
        '"conclusion":"建议采购|观察|暂停|终止","suggested_qty":整数,'
        '"reason":"100-180字综合分析，必须覆盖上述多个维度并给出权衡结论",'
        '"risks":["风险点数组"],"confidence":"高|中|低"}'
    )
    if festival_info:
        system += (
            "【重要：节日/季节性产品专用口径】本产品为节日产品，系统计算的【最新计算.库存天数】是按历史同期"
            "节日窗口日均推算的权威库存覆盖天数，请以此为准判断断货风险与补货需求。"
            "禁止用近30天日均重新计算库存覆盖天数或据此判断断货——节日产品旺季需求集中，"
            "即使近30天日均显示库存充足，只要【节日窗口口径.剩余缺口】>0 或【窗口口径库存覆盖天数】不足，"
            "就必须提示需提前补货；请结合节日窗口口径数据说明，避免出现\"库存充足/无断货风险\""
            "等与窗口口径矛盾的说法。"
        )
    user = json.dumps(input_data, ensure_ascii=False)

    input_snapshot = {"system": system, "user": user}
    try:
        content = await chat_completion([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], max_tokens=100000, timeout=180)  # 推理模型的 reasoning_tokens 会吃光输出预算导致正文为空；上下文长的产品给足预算
        parsed = _parse_json_text(content)
        output = {
            "status": "success",
            "forecast": parsed.get("forecast"),
            "factors": parsed.get("factors", {}),
            "conclusion": parsed.get("conclusion"),
            "suggested_qty": parsed.get("suggested_qty"),
            "reason": parsed.get("reason"),
            "risks": parsed.get("risks", []),
            "confidence": parsed.get("confidence"),
        }
        await _save_evaluation(session, asin, "purchase_advice", input_snapshot, output,
                               settings.DEEPSEEK_MODEL, "success", int((time.time() - started) * 1000))
        output["asin"] = asin
        output["model"] = settings.DEEPSEEK_MODEL
        return output
    except Exception as e:
        logger.warning(f"[{asin}] DeepSeek 评估失败，回退规则原因: {e}")
        fallback = {
            "status": "fallback",
            "asin": asin,
            "forecast": None,
            "conclusion": latest.purchase_level if latest else None,
            "suggested_qty": latest.suggested_qty if latest else None,
            "reason": _fallback_reason(latest, cost_table) if latest else "无历史数据",
            "risks": [],
            "confidence": "低",
            "error": str(e),
        }
        await _save_evaluation(session, asin, "purchase_advice", input_snapshot, fallback,
                               settings.DEEPSEEK_MODEL, "failed", int((time.time() - started) * 1000), str(e))
        return fallback


async def generate_daily_report_ai(summary: dict, session: AsyncSession) -> dict:
    """生成采购日报 AI 总结：整体总结 + 每条重点提醒的全面分析"""
    started = time.time()
    alerts = [
        {
            "asin": a.get("asin"),
            "产品名": a.get("product_name"),
            "负责人": a.get("operator"),
            "级别": a.get("purchase_level"),
            "提醒类型": a.get("alert_type"),
            "提醒原因": a.get("alert_reason") or a.get("purchase_trigger"),
            "评分": a.get("purchase_score"),
            "建议数量": a.get("suggested_qty"),
            "库存天数": a.get("inventory_days"),
            "原因": a.get("ai_analysis") or a.get("purchase_trigger"),
        }
        for a in summary.get("top_alerts", [])[:10]
    ]
    # 断货风险明细（重点提醒中已识别的风险项，供 AI 写入总结）
    stockout_details = [
        {
            "asin": a.get("asin"),
            "产品名": a.get("product_name"),
            "负责人": a.get("operator"),
            "库存天数": a.get("inventory_days"),
            "提醒原因": a.get("alert_reason"),
        }
        for a in summary.get("top_alerts", [])
        if a.get("alert_type") == "断货风险"
    ]
    input_snapshot = {
        "日期": summary.get("calc_date"),
        "总产品数": summary.get("total_asins"),
        "立即采购": summary.get("immediate_count"),
        "观察": summary.get("observe_count"),
        "暂停": summary.get("pause_count"),
        "断货风险数量": summary.get("stockout_count", 0),
        "暂停产品中断货风险数量": summary.get("pause_stockout_count", 0),
        "成本表三渠道盈利": {
            "海运盈利产品数": summary.get("cost_summary", {}).get("sea", 0),
            "空派盈利产品数": summary.get("cost_summary", {}).get("air", 0),
            "快递盈利产品数": summary.get("cost_summary", {}).get("express", 0),
            "三渠道全亏产品数": summary.get("cost_summary", {}).get("all_loss", 0),
        },
        "断货风险明细": stockout_details,
        "重点提醒": alerts,
    }
    system = (
        "你是亚马逊补货决策日报分析师。基于当天数据生成日报分析。"
        "注意：断货风险数量大于0时，总结中必须明确指出风险存在，绝不能写\"库存状况良好\"或\"未发现风险\"；"
        "要给出代表性 ASIN、库存覆盖天数与建议动作（立即补货/复核暂停产品）。"
        "输出 JSON：{\"summary\":\"150-300字整体总结。必须覆盖：①今日扫描产品总数与立即采购/观察/暂停数量；"
        "②断货风险数量、暂停产品中的风险数量、风险代表ASIN（含库存天数/补货周期）；"
        "③成本表三渠道盈利情况（海运/空派/快递盈利产品数、三渠道全亏产品数，全亏产品需提示终止加订风险）；"
        "④整体库存与销售趋势判断；⑤具体行动建议（哪些需立即补货、哪些暂停产品需复核、哪些需关注销量趋势）。"
        "若断货风险为0且无其他风险，才可写库存状况良好。\","
        "\"alerts\":[{\"asin\":\"B0XXX\",\"analysis\":\"50-100字全面分析（趋势/库存/利润/风险）\"}]}"
    )
    try:
        content = await chat_completion([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(input_snapshot, ensure_ascii=False)},
        ], max_tokens=100000, timeout=180)  # 日报要覆盖全量+多条 alerts，给足预算避免 reasoning_tokens 吃光正文
        parsed = _parse_json_text(content)
        analysis_map = {a.get("asin"): a.get("analysis") for a in parsed.get("alerts", [])}
        enriched = dict(summary)
        enriched["ai_summary"] = parsed.get("summary", "")
        for alert in enriched.get("top_alerts", []):
            if alert.get("asin") in analysis_map:
                alert["ai_analysis"] = analysis_map[alert["asin"]]
        await _save_evaluation(session, "__daily__", "daily_report", input_snapshot, parsed,
                               settings.DEEPSEEK_MODEL, "success", int((time.time() - started) * 1000))
        # 每条重点提醒的分析按 ASIN 落库，供日报页/详情页读取
        for asin, analysis in analysis_map.items():
            if asin and analysis:
                session.add(AiEvaluation(
                    asin=asin,
                    calc_date=date.today(),
                    eval_type="daily_alert",
                    input_data=json.dumps({"source": "daily_report"}, ensure_ascii=False),
                    output_data=json.dumps({"reason": analysis}, ensure_ascii=False),
                    model=settings.DEEPSEEK_MODEL,
                    status="success",
                ))
        await session.commit()
        return enriched
    except Exception as e:
        logger.warning(f"DeepSeek 日报生成失败，回退规则日报: {e}")
        await _save_evaluation(session, "__daily__", "daily_report", input_snapshot, {"error": str(e)},
                               settings.DEEPSEEK_MODEL, "failed", int((time.time() - started) * 1000), str(e))
        return dict(summary)


_TRANSPORT_METHODS = {"空运", "空派", "海运", "快递"}


def _normalize_date(value, fallback: date) -> date:
    """把模型输出的日期字符串解析为 date，失败返回 fallback"""
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            pass
    return fallback


def _normalize_ai_batches(raw, total_qty: int) -> list:
    """清洗 AI 返回的批次：限制批次数、数量取整、日期规范化、数量之和对齐总量 X。

    批次数最多5批；丢弃数量≤0或格式非法的批次；下单日期不早于今天，
    到货日期不早于下单日期；各批数量之和与 X 不一致时，把差额补/减到数量最大的一批。
    """
    if not isinstance(raw, list):
        return []
    today = date.today()
    cleaned = []
    for b in raw[:5]:
        if not isinstance(b, dict):
            continue
        try:
            qty = int(b.get("qty") or 0)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            continue
        order_date = _normalize_date(b.get("order_date"), today)
        if order_date < today:
            order_date = today
        arrival_date = _normalize_date(b.get("arrival_date"), order_date)
        if arrival_date < order_date:
            arrival_date = order_date
        method = (b.get("method") or "").strip()
        if method not in _TRANSPORT_METHODS:
            method = "海运"
        cleaned.append({
            "batch_no": len(cleaned) + 1,
            "qty": qty,
            "order_date": order_date.isoformat(),
            "arrival_date": arrival_date.isoformat(),
            "method": method,
            "reason": (b.get("reason") or "").strip()[:80],
        })
    if not cleaned:
        return []
    total = sum(b["qty"] for b in cleaned)
    if total != total_qty:
        delta = total_qty - total
        target = max(cleaned, key=lambda x: x["qty"])
        if target["qty"] + delta > 0:
            target["qty"] += delta
        else:
            # 差额过大无法微调 → 合并为单批，确保总量一致
            return [{
                "batch_no": 1,
                "qty": total_qty,
                "order_date": cleaned[0]["order_date"],
                "arrival_date": cleaned[0]["arrival_date"],
                "method": cleaned[0]["method"],
                "reason": cleaned[0]["reason"],
            }]
    return cleaned


async def plan_batches_ai(
    asin: str,
    suggested_qty: int,
    context: dict,
    session: AsyncSession,
) -> dict:
    """AI 分批补货规划：补货总量 X 确定后，基于多因素给出分批数量与下单/到货日期。

    context 由调用方组装（产品档案、库存、未来月度预测、运输时效、节日窗口、生命周期等）。
    返回 {"status","source","batches","total_qty","summary","confidence"}；
    status=success 且 batches 非空时调用方采用，否则回退规则版批次规划。
    """
    started = time.time()
    if suggested_qty <= 0:
        return {"status": "skipped", "source": "ai", "batches": [], "total_qty": 0}

    system = (
        "你是亚马逊跨境电商资深补货与供应链计划专家。系统已确定本次补货总量 X（件），"
        "请基于多因素把 X 拆分为若干批次，并为每批给出建议下单日期与预计到货日期。"
        "必须综合考虑：未来月度需求节奏、库存可售天数、补货周期、大货工期、各运输方式的时效与费用、"
        "节日/季节窗口、生命周期阶段、产品是否过季、箱规（各批数量须为箱规整数倍），"
        "以及资金占用与仓储成本。"
        "运输方式选择原则：优先选择费用最低的运输方式，通常为海运；"
        "只有在海运时效无法满足该批最晚到货时间（会导致断货或错过销售窗口）时，"
        "才可对该批改用空运/快递，并在 reason 中说明原因。"
        "拆批原则：在不造成断货的前提下尽量推迟下单，降低资金占用与仓储压力；"
        "确保每批到货时间能覆盖对应阶段需求，既避免断货，也避免一次性到货造成过度压货。"
        "所有日期必须为 YYYY-MM-DD 格式，下单日期不得早于今天，到货日期 = 下单日期 + 该批运输时效。"
        "各批次数量之和必须等于 X。批次数由你决定，最多5批。"
        "输出 JSON：{\"batches\":[{\"batch_no\":1,\"qty\":整数,\"order_date\":\"YYYY-MM-DD\","
        "\"arrival_date\":\"YYYY-MM-DD\",\"method\":\"海运|空派|快递\",\"reason\":\"该批拆批与日期依据(40字内)\"}],"
        "\"summary\":\"整体分批思路(80字内)\",\"confidence\":\"高|中|低\"}"
    )
    user = json.dumps(context, ensure_ascii=False, default=str)
    input_snapshot = {"system": system, "user": user, "suggested_qty": suggested_qty}
    try:
        content = await chat_completion([
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ], max_tokens=100000, timeout=180)  # 拆批上下文长、推理量大，给足预算避免 reasoning_tokens 吃光正文
        parsed = _parse_json_text(content)
        batches = _normalize_ai_batches(parsed.get("batches"), suggested_qty)
        if not batches:
            raise ValueError("AI 未返回有效批次")
        output = {
            "status": "success",
            "source": "ai",
            "batches": batches,
            "total_qty": sum(b["qty"] for b in batches),
            "summary": parsed.get("summary"),
            "confidence": parsed.get("confidence"),
        }
        await _save_evaluation(session, asin, "batch_plan", input_snapshot, output,
                               settings.DEEPSEEK_MODEL, "success", int((time.time() - started) * 1000))
        return output
    except Exception as e:
        err = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
        logger.warning(f"[{asin}] AI 分批补货规划失败，回退规则批次: {err}")
        fallback = {"status": "failed", "source": "ai", "batches": [], "error": err}
        await _save_evaluation(session, asin, "batch_plan", input_snapshot, fallback,
                               settings.DEEPSEEK_MODEL, "failed", int((time.time() - started) * 1000), err)
        return fallback
