# -*- coding: utf-8 -*-
"""采购计划表 — 领星 采购计划 listPlanBasicInfo 网页API 每日抓取落库

数据源：https://huizhixin.lingxing.com/api/module/purchase/plan/listPlanBasicInfo
接口返回 data 为 {plan_sn: [items...]} 字典结构，每个 key 是一个采购计划编号，
每个 value 是该计划下的明细 items 数组。本表按「计划-明细」逐行落库，保证完整数据入库。

  - 每个 item 一行：记录 plan_sn（明细上的计划编号）、group_plan_sn（外层字典 key）、
    采购数量 quantity_plan、状态 status/status_text、领星商品ID product_id 等。
  - 品名 product_name / 系统ASIN：采购计划明细接口无 product_name 字段，
    通过 product_id 关联系统 products（product_id 或 lx_id）回填品名与ASIN（尽力而为）。
  - 幂等：每次全量抓取先删旧数据再写入（每天完整数据入库，每天更新）。
  - 每条明细完整原始 JSON 另存 raw_data 兜底（不丢任何字段）。
"""

from datetime import date, datetime

from sqlalchemy import String, Integer, BigInteger, DateTime, Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchasePlan(Base):
    __tablename__ = "purchase_plans"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    plan_sn: Mapped[str | None] = mapped_column(String(50), index=True, comment="计划编号(明细 items.plan_sn)")
    group_plan_sn: Mapped[str | None] = mapped_column(String(50), index=True, comment="计划编号(外层字典key)")
    # ── 商品 ──
    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="领星商品ID")
    asin: Mapped[str | None] = mapped_column(String(20), index=True, comment="系统ASIN(经product_id关联回填)")
    product_name: Mapped[str | None] = mapped_column(Text, comment="品名(经product_id关联回填)")
    fnsku: Mapped[str | None] = mapped_column(String(50), comment="FNSKU")
    sid: Mapped[int | None] = mapped_column(BigInteger, comment="站点ID")
    # ── 采购信息 ──
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, comment="供应商ID")
    cg_uid: Mapped[int | None] = mapped_column(BigInteger, comment="采购员UID")
    quantity_plan: Mapped[int | None] = mapped_column(Integer, comment="计划采购数量")
    parent_id: Mapped[int | None] = mapped_column(BigInteger, comment="父级ID")
    is_urgent: Mapped[int | None] = mapped_column(Integer, comment="是否加急")
    is_related_process_plan: Mapped[int | None] = mapped_column(Integer, comment="是否关联工序计划")
    is_combo: Mapped[int | None] = mapped_column(Integer, comment="是否组合")
    step_price: Mapped[str | None] = mapped_column(Text, comment="阶梯价(JSON)")
    # ── 状态 ──
    status: Mapped[int | None] = mapped_column(Integer, comment="状态编码")
    status_text: Mapped[str | None] = mapped_column(String(20), index=True, comment="状态文本(待采购/已完成/已作废)")
    op_types: Mapped[str | None] = mapped_column(Text, comment="操作权限(JSON)")
    # 原始完整数据（兜底，保证不丢字段）
    raw_data: Mapped[str | None] = mapped_column(Text, comment="单条明细原始响应JSON")
    fetch_date: Mapped[date] = mapped_column(Date, index=True, comment="抓取日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
