# -*- coding: utf-8 -*-
"""采购计划明细项表（接口2 listNew）— 领星 采购计划 listNew 网页API 每日抓取落库

数据源：https://huizhixin.lingxing.com/api/module/purchase/plan/listNew
接口返回顶层为 {code, list: [...], total: N}，list 为采购计划单（分组）数组，
每个分组含 items 明细数组。本表按「明细 item」逐行落库，保证完整数据入库。

与 purchase_plans(接口1 listPlanBasicInfo) 的差异：
  - 接口1 明细无 product_name，需经 product_id 关联回填；
  - 接口2 listNew 明细 items 直接含 product_name / status_text / plan_sn，
    品名一栏采购所需字段完整，无需回填。

  - 每个 item 一行：plan_sn(明细计划编号)、product_name(品名)、status_text(状态)、
    ppg_sn(外层采购计划单号)、product_id、quantity_plan 等。
  - 幂等：每次全量抓取先删旧数据再写入（每天完整数据入库，每天更新）。
  - 每条明细完整原始 JSON 另存 raw_data 兜底（不丢任何字段）。
"""

from datetime import date, datetime

from sqlalchemy import String, Integer, BigInteger, DateTime, Date, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PurchasePlanItem(Base):
    __tablename__ = "purchase_plan_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # ── 计划编号 ──
    plan_sn: Mapped[str | None] = mapped_column(String(50), index=True, comment="计划编号(明细 items.plan_sn)")
    plan_id: Mapped[str | None] = mapped_column(String(50), comment="明细业务ID(如 1_PP260904002)")
    ppg_sn: Mapped[str | None] = mapped_column(String(50), index=True, comment="采购计划单号(外层分组 ppg_sn)")
    ppg_sn_id: Mapped[str | None] = mapped_column(String(50), comment="采购计划单业务ID")
    group_id: Mapped[int | None] = mapped_column(BigInteger, comment="外层分组元素id")
    create_time: Mapped[str | None] = mapped_column(String(50), comment="采购计划单创建时间(外层分组 create_time)")
    # ── 商品 ──
    product_id: Mapped[int | None] = mapped_column(BigInteger, index=True, comment="领星商品ID")
    product_name: Mapped[str | None] = mapped_column(Text, comment="品名(items.product_name)")
    product_model: Mapped[str | None] = mapped_column(String(100), comment="产品型号")
    sku: Mapped[str | None] = mapped_column(String(100), comment="本地SKU")
    fnsku: Mapped[str | None] = mapped_column(String(50), comment="FNSKU")
    brand_name: Mapped[str | None] = mapped_column(String(100), comment="品牌")
    category_name: Mapped[str | None] = mapped_column(String(200), comment="类目")
    # ── 采购信息 ──
    supplier_id: Mapped[int | None] = mapped_column(BigInteger, comment="供应商ID")
    supplier_name: Mapped[str | None] = mapped_column(String(50), comment="供应商名称")
    purchaser_id: Mapped[int | None] = mapped_column(BigInteger, comment="采购员UID")
    purchaser_name: Mapped[str | None] = mapped_column(String(50), comment="采购员名称")
    cg_uid: Mapped[int | None] = mapped_column(BigInteger, comment="采购组UID")
    sid: Mapped[int | None] = mapped_column(BigInteger, comment="站点ID")
    wid: Mapped[int | None] = mapped_column(BigInteger, comment="仓库ID")
    wid_name: Mapped[str | None] = mapped_column(String(50), comment="仓库名称")
    warehouse_name: Mapped[str | None] = mapped_column(String(50), comment="仓库名称(接口)")
    quantity_plan: Mapped[int | None] = mapped_column(Integer, comment="计划采购数量")
    quantity_purchased: Mapped[int | None] = mapped_column(Integer, comment="已采购数量")
    cases_num: Mapped[int | None] = mapped_column(Integer, comment="箱数")
    quantity_per_case: Mapped[int | None] = mapped_column(Integer, comment="每箱数量")
    pp_id: Mapped[int | None] = mapped_column(BigInteger, comment="采购计划ID")
    is_urgent: Mapped[int | None] = mapped_column(Integer, comment="是否加急")
    is_combo: Mapped[int | None] = mapped_column(Integer, comment="是否组合")
    is_aux: Mapped[int | None] = mapped_column(Integer, comment="是否辅料")
    has_relation_order: Mapped[int | None] = mapped_column(Integer, comment="是否有关联采购单")
    expect_arrive_time: Mapped[str | None] = mapped_column(String(50), comment="预计到货时间")
    # ── 状态 ──
    status: Mapped[int | None] = mapped_column(Integer, index=True, comment="状态编码")
    status_text: Mapped[str | None] = mapped_column(String(20), index=True, comment="状态文本(待采购/已完成/已作废)")
    group_status: Mapped[int | None] = mapped_column(Integer, comment="分组状态编码")
    row_index: Mapped[int | None] = mapped_column(Integer, comment="行序号")
    # 原始完整数据（兜底，保证不丢字段）
    raw_data: Mapped[str | None] = mapped_column(Text, comment="单条明细原始响应JSON")
    fetch_date: Mapped[date] = mapped_column(Date, index=True, comment="抓取日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
