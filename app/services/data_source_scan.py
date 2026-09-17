# -*- coding: utf-8 -*-
"""数据来源核验字典 — 自动扫描与预填

遍历系统所有数据表字段，生成"字段来源字典"初始清单，并按表/字段预填
数据来源(data_source)、采集方式(collect_method)、更新频率(update_freq)、分组(category)。

幂等：已存在 (table_name, field_name) 的记录保留用户编辑，仅补充缺失字段。
可单独运行：python -m app.services.data_source_scan
也可被 API /api/v1/data-source/scan 调用。
"""

import asyncio
from sqlalchemy import select

from app.database import Base, async_session_factory
from app.models.data_source_dict import DataSourceDict
from app import models  # noqa: F401  确保所有表已注册进 Base.metadata

# ── 表级默认映射（来源, 采集方式, 更新频率, 分组） ──
TABLE_META = {
    "products":                    ("领星(showOnline)", "网页API每日抓取", "每日", "产品"),
    "sales_data":                  ("领星(showOnline快照拆分)", "网页API每日抓取", "每日", "销量"),
    "daily_sales_snapshots":       ("领星(showOnline/新品快照)", "网页API每日抓取+SIF补充", "每日", "销量"),
    "inventory_snapshots":         ("领星(库存明细)", "网页API+MCP兜底", "每日", "库存"),
    "historical_monthly_stats":    ("领星(利润报表回填)+SIF(竞品)", "月度回填+每周SIF", "月度/每周", "销量"),
    "sales_statistics_reports":    ("领星(sales-statistics)", "网页API按日区间实抓(近30天/去年近30天)", "每日", "销量"),
    "daily_sales_stats":           ("领星(sales-statistics)", "网页API按日区间实抓(逐日)", "每日", "销量"),
    "calculation_results":         ("本地计算引擎(calculation_tasks)", "规则计算", "按等级频率", "计算"),
    "calculation_step_results":    ("本地计算引擎(计算步骤)", "规则计算", "实时", "计算"),
    "calculation_timeline_resets": ("排程调节器(人工)", "人工维护", "手动", "计算"),
    "ai_evaluations":              ("AI(DeepSeek)", "AI生成", "实时", "AI"),
    "product_costs":               ("本地成本计算+人工覆盖", "计算/人工", "实时/人工", "成本"),
    "festival_calendar":           ("基础数据(节日日历)", "人工维护/导入", "手动", "基础数据"),
    "festival_timing":             ("基础数据(节日时间点)", "人工维护/导入", "手动", "基础数据"),
    "tag_festival_map":            ("基础数据(标签-节日映射)", "人工维护", "手动", "基础数据"),
    "festival_lifecycle_days":     ("基础数据(节日生命周期)", "人工维护", "手动", "基础数据"),
    "category_leadtimes":          ("基础数据(分类工期)", "人工维护", "手动", "基础数据"),
    "operators":                   ("运营人员(从products同步+人工)", "自动同步/人工", "启动/手动", "基础数据"),
    "config_params":               ("系统配置参数", "人工维护(.env/页面)", "手动", "系统"),
    "sync_logs":                   ("系统同步任务日志", "系统记录", "每日", "系统"),
    "api_raw_responses":           ("领星/SIF原始接口响应", "原始接口抓取", "每日", "系统"),
}

# ── 业务维护/人工字段覆盖（(table, field) → (来源, 采集方式, 频率, 分组)） ──
# 这些字段不在领星原始数据里，是本地计算/人工维护/评分指标，需单独标注
FIELD_META = {
    # products 业务维护字段
    ("products", "life_cycle"):     ("本地计算+节日时间点表", "规则计算", "每日", "产品"),
    ("products", "product_level"):  ("本地计算(年销量分档)", "规则计算", "每日", "产品"),
    ("products", "calc_frequency"): ("本地计算(等级频率)", "规则计算", "每日", "产品"),
    ("products", "product_type"):   ("本地计算(节日长期判定)", "规则计算", "每日", "产品"),
    ("products", "product_stage"):  ("本地计算(新老品)", "规则计算", "每日", "产品"),
    ("products", "festival"):       ("本地计算(标签映射)", "规则计算", "每日", "产品"),
    ("products", "core_months"):    ("本地计算(节日月份)", "规则计算", "每日", "产品"),
    ("products", "lead_time"):      ("分类工期表+人工", "人工维护", "手动", "产品"),
    ("products", "box_quantity"):   ("领星(箱规)", "网页API每日抓取", "每日", "产品"),
    ("products", "min_order_qty"):  ("人工维护", "人工维护", "手动", "产品"),
    ("products", "list_date"):      ("本地计算(上架日期)", "规则计算", "每日", "产品"),
    ("products", "operator"):       ("人工维护", "人工维护", "手动", "产品"),
    ("products", "primary_operator"): ("本地计算(主运营)", "规则计算", "每日", "产品"),
    ("products", "is_new"):         ("本地计算(新品标记)", "规则计算", "每日", "产品"),
    ("products", "profit_rate"):    ("领星(毛利报表回填)+本地", "月度回填/计算", "月度", "产品"),
    ("products", "acos_30d"):       ("领星(广告)+可手动", "每日抓取/人工", "每日", "产品"),
    ("products", "fba_available_days"): ("领星(库存口径)", "网页API每日抓取", "每日", "产品"),
    ("products", "stockout_date"):  ("领星(库存口径)", "网页API每日抓取", "每日", "产品"),
    ("products", "estimated_daily_sales"): ("领星(库存口径)", "网页API每日抓取", "每日", "产品"),
    ("products", "purchase_on_order"): ("领星(采购待到货)", "网页API每日抓取", "每日", "产品"),
    # calculation_results 关键指标
    ("calculation_results", "forecast_total"): ("本地计算引擎(预测)", "公式计算", "按等级频率", "计算"),
    ("calculation_results", "available_stock"): ("库存快照+在途", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "inventory_days"): ("本地计算(覆盖天数)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "replenishment_cycle"): ("本地计算(补货周期)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "urgency_score"): ("本地计算(紧急评分)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "purchase_trigger"): ("本地计算(触发判断)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "suggested_qty"): ("本地计算(建议量)", "公式计算", "按等级频率", "计算"),
    ("calculation_results", "batch_plan"): ("本地计算(批次规划)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "purchase_score"): ("本地计算(评分模型)", "多因素加权", "按等级频率", "计算"),
    ("calculation_results", "base_score"): ("本地计算(公式评分)", "多因素加权", "按等级频率", "计算"),
    ("calculation_results", "purchase_level"): ("本地计算(采购等级)", "评分分档", "按等级频率", "计算"),
    ("calculation_results", "score_detail"): ("本地计算(得分明细)", "规则计算", "按等级频率", "计算"),
    ("calculation_results", "operator_confirmed"): ("人工确认(运营)", "人工维护", "手动", "计算"),
    ("calculation_results", "confirmed_qty"): ("人工确认(运营)", "人工维护", "手动", "计算"),
    ("calculation_results", "confirmed_at"): ("人工确认(运营)", "人工维护", "手动", "计算"),
    ("calculation_results", "user_feedback"): ("人工反馈(AI参考)", "人工维护", "手动", "计算"),
    ("calculation_results", "feedback_at"): ("人工反馈(时间)", "人工维护", "手动", "计算"),
    ("calculation_results", "lx_available_days"): ("领星口径(交叉验证)", "网页API", "按等级频率", "计算"),
    ("calculation_results", "lx_stockout_date"): ("领星口径(交叉验证)", "网页API", "按等级频率", "计算"),
    ("calculation_results", "lx_estimated_daily_sales"): ("领星口径(交叉验证)", "网页API", "按等级频率", "计算"),
    # ai_evaluations
    ("ai_evaluations", "conclusion"): ("AI(DeepSeek)", "AI生成", "实时", "AI"),
    ("ai_evaluations", "suggested_qty"): ("AI(DeepSeek)", "AI生成", "实时", "AI"),
}

# 需要跳过的基础表字段
SKIP_FIELDS = {"id", "created_at", "updated_at"}


def _default_meta(table_name: str):
    """取表级默认元信息；未注册表给兜底"""
    return TABLE_META.get(table_name, ("未知", "未知", "未知", "其他"))


async def scan_data_source(session) -> dict:
    """扫描并写回数据来源字典，返回汇总"""
    tables = Base.metadata.tables
    added = 0
    updated = 0
    skipped = 0
    existing = {
        (r.table_name, r.field_name)
        for r in (await session.execute(select(DataSourceDict))).scalars()
    }

    for table_name, table in tables.items():
        d_source, d_method, d_freq, d_cat = _default_meta(table_name)
        for col_name, col in table.columns.items():
            if col_name in SKIP_FIELDS:
                skipped += 1
                continue
            key = (table_name, col_name)
            source, method, freq, cat = FIELD_META.get(key, (d_source, d_method, d_freq, d_cat))
            # field 注释优先取数据库 comment
            comment = getattr(col, "comment", None)
            if key in existing:
                # 已存在：若来源仍为"未知"，尝试补全；否则保留用户编辑
                row = (await session.execute(
                    select(DataSourceDict).where(
                        DataSourceDict.table_name == table_name,
                        DataSourceDict.field_name == col_name,
                    )
                )).scalar_one_or_none()
                if row is not None:
                    row.field_comment = row.field_comment or comment
                    if not row.data_source or row.data_source == "未知":
                        row.data_source = source
                        row.collect_method = method
                        row.update_freq = freq
                        row.category = cat
                        updated += 1
                continue
            row = DataSourceDict(
                table_name=table_name,
                field_name=col_name,
                field_comment=comment,
                data_source=source,
                collect_method=method,
                update_freq=freq,
                category=cat,
            )
            session.add(row)
            added += 1
    await session.commit()
    return {"total_scan": len(tables), "added": added, "updated": updated, "skipped": skipped}


async def _main():
    async with async_session_factory() as session:
        res = await scan_data_source(session)
        print(f"扫描完成: {res}")
        total = len((await session.execute(select(DataSourceDict))).scalars().all())
        print(f"字典总行数: {total}")


if __name__ == "__main__":
    asyncio.run(_main())
