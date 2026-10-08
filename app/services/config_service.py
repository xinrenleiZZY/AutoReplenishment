"""配置参数服务：ConfigParam 表读写，未配置时回退到 settings 默认值"""

import json
import logging
import re
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.config import ConfigParam

logger = logging.getLogger(__name__)

# 可自定义参数定义：key -> (默认值来源, 说明, 类型)
PARAM_DEFS: dict[str, tuple] = {
    "calc_frequencies": (settings.CALC_FREQUENCIES, "按等级计算频率（天），格式 S:1,A:3,B:5,C:7,D:14", "str"),
    "calc_frequency_default": (settings.CALC_FREQUENCY_DEFAULT, "无等级/未配置等级时的默认频率（天）", "int"),
    "report_lifecycles": (settings.REPORT_LIFECYCLES, "日报分析生命周期自定义（逗号分隔，可多选：启动期/增长期/热卖期/成熟期/下降期/未知；留空=全部生命周期不过滤）", "str"),
    "forecast_months": (settings.FORECAST_MONTHS, "未来预测月数", "int"),
    "safe_stock_days": (settings.SAFE_STOCK_DAYS, "安全库存天数", "int"),
    "sea_slow_days": (settings.SEA_SLOW_DAYS, "海运淡季时效（天）", "int"),
    "sea_peak_days": (settings.SEA_PEAK_DAYS, "海运旺季时效（天）", "int"),
    "air_slow_days": (settings.AIR_SLOW_DAYS, "空派淡季时效（天）", "int"),
    "air_peak_days": (settings.AIR_PEAK_DAYS, "空派旺季时效（天）", "int"),
    "express_slow_days": (settings.EXPRESS_SLOW_DAYS, "快递淡季时效（天）", "int"),
    "express_peak_days": (settings.EXPRESS_PEAK_DAYS, "快递旺季时效（天）", "int"),
    "inventory_danger_max_days": (settings.INVENTORY_DANGER_MAX_DAYS, "库存紧急-危险上限（库存天数 < 该值 → 危险）", "int"),
    "inventory_low_max_days": (settings.INVENTORY_LOW_MAX_DAYS, "库存紧急-偏低上限（危险上限 ≤ 库存天数 < 该值 → 偏低）", "int"),
    "inventory_healthy_max_days": (settings.INVENTORY_HEALTHY_MAX_DAYS, "库存紧急-健康上限（偏低上限 ≤ 库存天数 ≤ 该值 → 健康，> 该值 → 过量）", "int"),
    "new_product_acos_max": (settings.NEW_PRODUCT_ACOS_MAX, "新品ACOS硬性指标（小数，如0.55）", "str"),
    "new_product_min_selling_days": (settings.NEW_PRODUCT_MIN_SELLING_DAYS, "新品剩余售卖天数门槛（低于该值终止加订）", "int"),
    "new_product_trigger_days": (settings.NEW_PRODUCT_TRIGGER_DAYS, "新品触发-连续天数（日均单量超阈值的天数）", "int"),
    "new_product_trigger_min_order": (settings.NEW_PRODUCT_TRIGGER_MIN_ORDER, "新品触发-日均单量阈值（单）", "int"),
    "ai_auto_evaluate": (settings.AI_AUTO_EVALUATE, "自动AI综合评估（新老品，仅对需要决策的产品；当天已评估则跳过）", "int"),
    "sea_slow_fee": (settings.SEA_SLOW_FEE, "海运运费-淡季（人民币/件）", "int"),
    "sea_peak_fee": (settings.SEA_PEAK_FEE, "海运运费-旺季（人民币/件）", "int"),
    "air_slow_fee": (settings.AIR_SLOW_FEE, "空派运费-淡季（人民币/件）", "int"),
    "air_peak_fee": (settings.AIR_PEAK_FEE, "空派运费-旺季（人民币/件）", "int"),
    "express_slow_fee": (settings.EXPRESS_SLOW_FEE, "快递运费-淡季（人民币/件）", "int"),
    "express_peak_fee": (settings.EXPRESS_PEAK_FEE, "快递运费-旺季（人民币/件）", "int"),
    "usd_cny_rate": (settings.USD_CNY_RATE, "人民币兑美元汇率（运费换算用）", "str"),
    "cost_exchange_rate": (settings.COST_EXCHANGE_RATE, "成本表汇率（实时汇率自动更新）", "str"),
    "fx_updated_at": ("", "实时汇率更新时间", "str"),
    "cost_extra_fees_usd": (settings.COST_EXTRA_FEES_USD, "成本表单件附加费用（美元/件，JSON）：如 {\"packing_card\":0.26,\"inbound\":0.32,\"storage\":0.11,\"ad\":2.55,\"return_loss\":0.45,\"misc\":0.14,\"over_threshold_loss\":0}，矫正三渠道Profit时逐项扣减", "str"),
    "operator_whitelist": (settings.OPERATOR_WHITELIST, "运营人员白名单（逗号分隔，仅保留这些负责人用于日报@；空=全部）", "str"),
    "feishu_group_whitelist": (settings.FEISHU_GROUP_WHITELIST, "飞书日报推送群白名单（多个群ID用逗号分隔）", "str"),
    "listing_sync_mode": (settings.LISTING_SYNC_MODE, "Listing同步方式：full=全量（旧）/ by_create_time=按创建时间区间", "str"),
    "listing_create_start": (settings.LISTING_CREATE_START, "Listing创建时间-起始（YYYY-MM-DD，含当天）", "str"),
    "listing_create_end": (settings.LISTING_CREATE_END, "Listing创建时间-结束（YYYY-MM-DD，含当天）", "str"),
    "listing_exclude_asins": (settings.LISTING_EXCLUDE_ASINS, "ASIN排除列表（逗号/换行分隔；同步/清洗时强制停用并标记「已排除」，仍会落库）", "str"),
    "listing_keep_asins": (settings.LISTING_KEEP_ASINS, "ASIN保留列表（逗号/换行分隔；优先级高于排除，清洗时不被覆盖状态、且不会被自动标记已删除）", "str"),
    "asin_list_updated_at": ("", "ASIN列表最近一次手动操作时间（排除/保留/添加/删除）", "str"),
    "festival_year_base": (0, "节日日历年份基准（内部标记：节日日期当前对齐的年份，每年12-31 23:59 自动+1）", "int"),
    "asin_list_refresh_pending": ("0", "ASIN列表存在待刷新操作（1=待刷新，由每10分钟任务重新清洗后清零）", "int"),
    "decoration_buffer_days": (settings.DECORATION_BUFFER_DAYS, "节日产品缓冲天数-装饰类", "int"),
    "non_decoration_buffer_days": (settings.NON_DECORATION_BUFFER_DAYS, "节日产品缓冲天数-非装饰/DIY", "int"),
    "long_term_safety_factor": (settings.LONG_TERM_SAFETY_FACTOR, "长期产品补货安全系数", "str"),
    "long_term_level_safety_factor": (settings.LONG_TERM_LEVEL_SAFETY_FACTOR, "新品&长期产品等级评定安全系数（等级＝基准×365×该值）", "str"),
    "sync_time": (settings.SYNC_TIME, "每日数据同步时间（HH:MM）", "str"),
    "daily_report_time": (settings.DAILY_REPORT_TIME, "每日计算+日报推送时间（HH:MM）", "str"),
    "product_level_mode": (settings.PRODUCT_LEVEL_MODE, "产品等级口径：mixed=老品按去年总销量+新品按近30天年化（方案D）/ annualize=全部近30天年化（方案C）", "str"),
    "product_type_long_tags": ("长期,西部牛仔", "长期产品特例标签（逗号分隔）；listing 标签命中即为长期产品，如 长期、西部牛仔", "str"),
    "product_type_festival_tags": ("", "节日产品特例标签（逗号分隔）；listing 标签命中即为节日产品（节日取标签映射，无映射用标签本身）", "str"),
    "new_product_name_keywords": ("26版,27版", "新品判定品名特例关键词（逗号分隔）；品名命中即为新品（老品ASIN复用场景）", "str"),
    "product_type_old_tags": ("19年前,18年前", "老品判定特例标签（逗号分隔）；listing 标签命中即为老品", "str"),
    "new_product_base_days": (3, "新品等级评定基准销量统计天数（近N天日均，默认3）", "int"),
    "lifecycle_coeff": ('{"启动期":[1.03,1.0],"增长期":[1.15,1.15],"热卖期":[1.3,1.3],"成熟期":[1.0,1.2],"下降期":[0.7,1.0]}',
                        "新品等级评定各生命周期阶段系数（JSON，[预测销量系数,安全系数]）；启动期按 系数^(天数/10) 逐日累加", "str"),
    "decline_profit_threshold": ("0.35", "下降期巨大利润采购阈值（海运毛利率≥该值允许采购，否则不采购）", "str"),
    "no_replenish_stock_cap": ("2000", "库存充足不加订阈值（FBA可售+在途合计≥该值直接不加订）", "int"),
}


# 参数 key -> settings 属性名
# 大量计算代码直接读 settings.XXX（不经 config_service），故需把 DB 已覆盖值写回全局 settings 单例
SETTINGS_ATTR_MAP: dict[str, str] = {
    "safe_stock_days": "SAFE_STOCK_DAYS",
    "sea_slow_days": "SEA_SLOW_DAYS",
    "sea_peak_days": "SEA_PEAK_DAYS",
    "air_slow_days": "AIR_SLOW_DAYS",
    "air_peak_days": "AIR_PEAK_DAYS",
    "express_slow_days": "EXPRESS_SLOW_DAYS",
    "express_peak_days": "EXPRESS_PEAK_DAYS",
    "sea_slow_fee": "SEA_SLOW_FEE",
    "sea_peak_fee": "SEA_PEAK_FEE",
    "air_slow_fee": "AIR_SLOW_FEE",
    "air_peak_fee": "AIR_PEAK_FEE",
    "express_slow_fee": "EXPRESS_SLOW_FEE",
    "express_peak_fee": "EXPRESS_PEAK_FEE",
    "usd_cny_rate": "USD_CNY_RATE",
    "cost_exchange_rate": "COST_EXCHANGE_RATE",
    "forecast_months": "FORECAST_MONTHS",
    "inventory_danger_max_days": "INVENTORY_DANGER_MAX_DAYS",
    "inventory_low_max_days": "INVENTORY_LOW_MAX_DAYS",
    "inventory_healthy_max_days": "INVENTORY_HEALTHY_MAX_DAYS",
    "decoration_buffer_days": "DECORATION_BUFFER_DAYS",
    "non_decoration_buffer_days": "NON_DECORATION_BUFFER_DAYS",
    "long_term_safety_factor": "LONG_TERM_SAFETY_FACTOR",
    "long_term_level_safety_factor": "LONG_TERM_LEVEL_SAFETY_FACTOR",
    "new_product_acos_max": "NEW_PRODUCT_ACOS_MAX",
    "new_product_min_selling_days": "NEW_PRODUCT_MIN_SELLING_DAYS",
    "new_product_trigger_days": "NEW_PRODUCT_TRIGGER_DAYS",
    "new_product_trigger_min_order": "NEW_PRODUCT_TRIGGER_MIN_ORDER",
}


async def _get_row(session: AsyncSession, key: str) -> ConfigParam | None:
    result = await session.execute(select(ConfigParam).where(ConfigParam.param_key == key))
    return result.scalar_one_or_none()


def apply_settings_override(key: str, value) -> bool:
    """把单个参数值写回全局 settings 单例（仅限 SETTINGS_ATTR_MAP 登记的 key）

    按 settings 属性的真实类型转换（PARAM_DEFS 的类型标注不完全准确，如 usd_cny_rate 实为 float）。
    """
    attr = SETTINGS_ATTR_MAP.get(key)
    if not attr or not hasattr(settings, attr):
        return False
    current = getattr(settings, attr)
    try:
        if isinstance(current, bool):
            casted = str(value).strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(current, int):
            casted = int(float(value))
        elif isinstance(current, float):
            casted = float(value)
        else:
            casted = str(value)
    except (ValueError, TypeError):
        return False
    setattr(settings, attr, casted)
    return True


async def sync_settings_overrides(session: AsyncSession) -> dict:
    """把 DB 中已覆盖的参数一次性同步到全局 settings（启动时调用）"""
    rows = await session.execute(select(ConfigParam))
    stored = {r.param_key: r.param_value for r in rows.scalars().all()}
    applied = {}
    for key in SETTINGS_ATTR_MAP:
        raw = stored.get(key)
        if raw is None:
            continue
        if apply_settings_override(key, raw):
            applied[key] = getattr(settings, SETTINGS_ATTR_MAP[key])
    return applied


def _cast(value: str, value_type: str):
    if value_type == "int":
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    return value


async def get_param(session: AsyncSession, key: str):
    """获取参数值（DB 优先，未配置回退 settings 默认）"""
    row = await _get_row(session, key)
    if row is not None:
        default, _desc, value_type = PARAM_DEFS.get(key, (None, "", "str"))
        return _cast(row.param_value, value_type)
    if key in PARAM_DEFS:
        return PARAM_DEFS[key][0]
    return None


async def get_all_params(session: AsyncSession) -> list[dict]:
    """列出全部可自定义参数（含当前值/默认值/说明/类型）"""
    rows = await session.execute(select(ConfigParam))
    stored = {r.param_key: r.param_value for r in rows.scalars().all()}
    result = []
    for key, (default, desc, value_type) in PARAM_DEFS.items():
        raw = stored.get(key)
        value = _cast(raw, value_type) if raw is not None else default
        result.append({
            "key": key,
            "value": value,
            "default": default,
            "description": desc,
            "type": value_type,
            "is_override": raw is not None,
        })
    return result


async def set_param(session: AsyncSession, key: str, value) -> dict:
    """写入参数（实时生效于后续计算）"""
    if key not in PARAM_DEFS:
        raise KeyError(f"未知参数: {key}")
    default, _desc, value_type = PARAM_DEFS[key]
    casted = _cast(str(value), value_type)
    if casted is None:
        raise ValueError(f"参数 {key} 值无效: {value}")
    row = await _get_row(session, key)
    if row is None:
        row = ConfigParam(param_key=key, param_value=str(casted))
        session.add(row)
    else:
        row.param_value = str(casted)
    await session.commit()
    apply_settings_override(key, casted)
    return {"key": key, "value": casted, "default": default, "type": value_type}


def split_asins(raw) -> set:
    """拆分逗号/分号/空白分隔的 ASIN 串"""
    return {a for a in re.split(r"[\s,;，；]+", str(raw or "")) if a}


async def touch_asin_list(session: AsyncSession) -> str:
    """记录 ASIN 列表最近一次手动操作时间，并置「待刷新」标记（由每10分钟任务消费）"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    await set_param(session, "asin_list_updated_at", now)
    await set_param(session, "asin_list_refresh_pending", 1)
    return now


async def apply_asin_action(session: AsyncSession, action: str, asins: list) -> dict:
    """ASIN 列表动作（保留列表优先级高于排除列表）

    - exclude / add_exclude：并入排除列表、移出保留列表 → 库中该 ASIN 立即置为「已排除」
    - keep / add_keep：并入保留列表、移出排除列表 → 库中该 ASIN 立即恢复「在售」
    - remove_exclude：仅从排除列表移除（不改产品状态，等下次清洗按规则重新判定）
    - remove_keep：仅从保留列表移除（同上）
    动作完成后记录操作时间并置待刷新标记。
    """
    from app.models.product import Product

    targets = sorted({(a or "").strip().upper() for a in (asins or []) if (a or "").strip()})
    if not targets:
        raise ValueError("ASIN 列表为空")

    exclude = split_asins(await get_param(session, "listing_exclude_asins"))
    keep = split_asins(await get_param(session, "listing_keep_asins"))
    tset = set(targets)

    if action in ("exclude", "add_exclude"):
        exclude |= tset
        keep -= tset
    elif action in ("keep", "add_keep"):
        keep |= tset
        exclude -= tset
    elif action == "remove_exclude":
        exclude -= tset
    elif action == "remove_keep":
        keep -= tset
    else:
        raise ValueError(f"未知动作: {action}")

    await set_param(session, "listing_exclude_asins", ",".join(sorted(exclude)))
    await set_param(session, "listing_keep_asins", ",".join(sorted(keep)))

    # 仅「加入」类动作立即改变产品状态；「移除」类不动状态（等下次清洗重新判定）
    changed = 0
    if action in ("exclude", "add_exclude"):
        result = await session.execute(
            update(Product)
            .where(Product.asin.in_(targets), Product.status == True)  # noqa: E712
            .values(status=False, status_text="已排除")
        )
        changed = result.rowcount or 0
        await session.commit()
    elif action in ("keep", "add_keep"):
        result = await session.execute(
            update(Product)
            .where(Product.asin.in_(targets), Product.status == False)  # noqa: E712
            .values(status=True, status_text="在售")
        )
        changed = result.rowcount or 0
        await session.commit()

    await touch_asin_list(session)
    return {
        "action": action,
        "asins": targets,
        "status_changed": changed,
        "exclude_list_count": len(exclude),
        "keep_list_count": len(keep),
    }


async def get_calc_frequencies(session: AsyncSession) -> dict:
    """获取按等级计算频率映射（DB 可覆盖）"""
    raw = await get_param(session, "calc_frequencies")
    parsed = {}
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        k, _, v = part.partition(":")
        try:
            parsed[k.strip().upper()] = int(v.strip())
        except ValueError:
            continue
    return parsed
