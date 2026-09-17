"""运营人员自动同步：从产品「负责人」字段解析姓名并写入运营人员表"""

import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.operator import Operator
from app.models.product import Product

logger = logging.getLogger(__name__)

# 角色前缀（如 运营-赵玉筠 / 采购部-古金萍 / TEMU运营-丁子铫）
_ROLE_PREFIX_RE = re.compile(r"^(?:TEMU)?(?:运营|采购部|采购|开发|产品|销售|海外仓)[-–—]?")
# 尾部数字/百分比后缀（如 何紫欣1.3% / 赵玉筠3）
_SUFFIX_RE = re.compile(r"\d+(?:\.\d+)?%?$")
# 已删除标记（如 卢俊(已删除) / 卢俊1.8%(已删除)）
_DELETED_RE = re.compile(r"[（(]?已删除[）)]?$")


def clean_operator_name(raw: str) -> str:
    """清洗负责人名称：去角色前缀、去尾部数字/百分比后缀"""
    name = (raw or "").strip()
    name = _DELETED_RE.sub("", name)
    name = _ROLE_PREFIX_RE.sub("", name)
    name = _SUFFIX_RE.sub("", name)
    return name.strip()


def clean_operator_field(raw: str) -> str:
    """清洗负责人字段（逗号分隔多个姓名）：逐段清洗、去重、逗号连接"""
    parts = []
    for part in str(raw or "").split(","):
        name = clean_operator_name(part)
        if name and name not in parts:
            parts.append(name)
    return ",".join(parts)


async def sync_operators_from_products(session: AsyncSession) -> dict:
    """解析全部产品负责人（逗号分隔、去重、清洗），按姓名唯一写入运营人员表（幂等）

    - 只新增不存在的姓名；清洗后相同的旧脏名行自动停用，保留干净姓名
    - 配置了白名单时，仅保留白名单内负责人，其余运营人员自动停用
    - 不删除已有人员（保留手动停用/备注）
    - 返回 {total, created, enabled}
    """
    from app.services.config_service import get_param

    whitelist_raw = await get_param(session, "operator_whitelist") or ""
    whitelist = {name.strip() for name in str(whitelist_raw).split(",") if name.strip()}

    rows = await session.execute(
        select(Product.operator).where(Product.operator.isnot(None))
    )
    raw_names: set[str] = set()
    for (val,) in rows.all():
        for part in str(val).split(","):
            name = part.strip()
            if name:
                raw_names.add(name)

    # 原始名 → 清洗名 映射（一个清洗名可能对应多个脏原始名）
    cleaned: dict[str, set[str]] = {}
    for raw in raw_names:
        name = clean_operator_name(raw)
        if name:
            cleaned.setdefault(name, set()).add(raw)
    if whitelist:
        # 只保留白名单内人员；白名单中暂未出现在产品的也先建出
        cleaned = {name: raw_set for name, raw_set in cleaned.items() if name in whitelist}
        for name in whitelist:
            cleaned.setdefault(name, {name})

    created = 0
    disabled = 0
    for name, raw_set in sorted(cleaned.items()):
        operator = (await session.execute(
            select(Operator).where(Operator.name == name)
        )).scalar_one_or_none()
        if operator is None:
            operator = Operator(name=name, role="负责人", status=True)
            session.add(operator)
            created += 1
        # 停用遗留的脏名行（与清洗名不同，且来源相同）
        for raw in raw_set:
            if raw == name:
                continue
            legacy = (await session.execute(
                select(Operator).where(Operator.name == raw)
            )).scalar_one_or_none()
            if legacy is not None and legacy.id != operator.id and legacy.status:
                legacy.status = False
                disabled += 1

    # 白名单模式：白名单外的人员全部停用
    if whitelist:
        all_ops = (await session.execute(select(Operator))).scalars().all()
        for op in all_ops:
            if op.name not in whitelist and op.status:
                op.status = False
                disabled += 1

    await session.commit()
    logger.info(f"运营人员同步: 新增 {created}, 停用 {disabled}, 共 {len(cleaned)} 人（白名单={bool(whitelist)}）")
    return {"total": len(cleaned), "created": created, "disabled": disabled, "whitelist": bool(whitelist)}
