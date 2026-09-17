"""
领星产品数据导入脚本
从 p_id/msku_id_full.json 读取全量产品数据，写入 PostgreSQL products 表
"""

import asyncio
import json
import os
import sys
import re
import logging
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import async_session_factory
from app.models.product import Product
from app.services.operator_sync import clean_operator_field
from app.services.tag_festival import classify_product
from sqlalchemy import select, update

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _parse_asin_list(raw) -> set:
    """解析 ASIN 列表配置（逗号/换行/空格分隔，转大写）"""
    if not raw:
        return set()
    return {a.strip().upper() for a in re.split(r"[\s,;，；\n\t]+", str(raw)) if a.strip()}


def _date_in_range(date_str, start: str, end: str) -> bool:
    """判断 YYYY-MM-DD 是否在 [start, end] 区间（含边界）"""
    d = str(date_str or "")[:10]
    if len(d) != 10:
        return True  # 无创建时间不排除
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _load_erp_create_time_map() -> dict:
    """ERP 创建时间：box_qty_raw.jsonl(sku→create_time) 映射到 ASIN"""
    try:
        from scripts.merge_product_sources import load_listing, load_erp
    except Exception as e:
        logger.warning(f"加载 ERP 创建时间失败: {e}")
        return {}
    by_asin, msku_map, local_sku_map, local_name_map = load_listing()
    erp = load_erp()
    result = {}
    for sku, item in erp.items():
        ct = item.get("create_time")
        if not ct:
            continue
        asin = msku_map.get(sku) or local_sku_map.get(sku)
        if not asin:
            pn = (item.get("product_name") or "").strip()
            if pn in local_name_map:
                asin = local_name_map[pn]
        if asin:
            result[asin] = str(ct)[:10]
    return result


def parse_category_text(category_text: str) -> tuple:
    """解析 category_text 如 '纸质印刷类\纸质套装类' → (一级分类, 二级分类)"""
    if not category_text:
        return (None, None)
    parts = category_text.split("\\")
    if len(parts) >= 2:
        return (parts[0].strip(), parts[1].strip())
    return (parts[0].strip(), None)


def parse_open_date(open_date_str) -> date:
    """解析 open_date '2025-05-08 17:55:20 PDT' → date"""
    if not open_date_str:
        return None
    try:
        # 格式如 '2025-05-08 17:55:20 PDT'
        match = re.match(r"(\d{4}-\d{2}-\d{2})", str(open_date_str))
        if match:
            return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except Exception:
        pass
    return None


def parse_small_rank(small_rank) -> str:
    """解析 small_rank JSON → 存为文本"""
    if not small_rank:
        return None
    if isinstance(small_rank, (list, dict)):
        return json.dumps(small_rank, ensure_ascii=False)
    return str(small_rank)


def _to_int(v):
    """安全转 int：空值/非法返回 None"""
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _to_float(v):
    """安全转 float：空值/非法返回 None"""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _to_str(v):
    if v is None or v == "":
        return None
    return str(v)


def _to_json(v):
    """dict/list → JSON 文本；空/非结构返回 None"""
    if not v:
        return None
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def load_tags() -> dict:
    """读取 tags_full.json：ASIN -> tagName 列表"""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "p_id", "tags_full.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    result = {}
    for asin, tags in raw.items():
        names = []
        for t in tags or []:
            name = (t.get("tagName") or "").strip()
            if name and name not in names:
                names.append(name)
        if names:
            result[asin.strip()] = json.dumps(names, ensure_ascii=False)
    return result


async def import_products(json_path: str) -> int:
    """从 JSON 全量导入产品数据（安全更新：只更新基础字段，不覆盖等级/工期/箱规等维护字段）

    返回更新/插入的记录数。
    """
    logger.info(f"读取产品数据: {json_path}")

    with open(json_path, "r", encoding="utf-8") as f:
        items = json.load(f)

    logger.info(f"共 {len(items)} 条产品记录")
    tags_map = load_tags()

    session = async_session_factory()
    try:
        async with session:
            # ── 读取同步配置（方式/创建时间区间/排除/保留） ──
            from app.services.config_service import get_param

            mode = (await get_param(session, "listing_sync_mode")) or "full"
            create_start = (await get_param(session, "listing_create_start")) or ""
            create_end = (await get_param(session, "listing_create_end")) or ""
            keep = _parse_asin_list(await get_param(session, "listing_keep_asins"))
            exclude = _parse_asin_list(await get_param(session, "listing_exclude_asins")) - keep
            long_tags = (await get_param(session, "product_type_long_tags")) or "长期,西部牛仔"
            fest_tags = (await get_param(session, "product_type_festival_tags")) or ""
            # 标签映射查库（缺省回退默认种子）+ 节日时间点表节日名兜底
            from app.services.tag_festival import load_festival_maps, load_calendar_names

            festival_map, season_map = await load_festival_maps(session)
            calendar_names = await load_calendar_names(session)

            erp_create_map = {}
            if mode == "by_create_time":
                erp_create_map = _load_erp_create_time_map()

            data_map = {}
            errors = 0
            for idx, item in enumerate(items):
                asin = item.get("asin", "").strip()
                if not asin:
                    continue

                try:
                    category, sub_category = parse_category_text(item.get("category_text", ""))

                    # 品名：local_name（领星产品管理品名）优先，其次 Amazon 标题 item_name
                    product_name = (
                        item.get("local_name")
                        or item.get("product_name")
                        or item.get("item_name")
                        or ""
                    ).strip()[:2000]

                    # 产品类型按 listing 标签：长期特例（如 长期、西部牛仔）优先=长期产品；
                    # 其次节日/季节标签=节日产品；否则=长期产品
                    festival_val, product_type = classify_product(
                        tags_map.get(asin), long_tags, fest_tags, festival_map, season_map, calendar_names)

                    # 状态：以 listing 为准，仅「在售」启用；停售/已删除一律停用，
                    # 排除列表中的 ASIN 会在下方统一标记「已排除」
                    status_text = str(item.get("status_text") or "")
                    is_active = status_text == "在售"

                    values = {
                        # 身份标识
                        "lx_id": _to_int(item.get("id")),
                        "store_id": _to_int(item.get("store_id")),
                        "msku": _to_str(item.get("msku")),
                        "local_sku": _to_str(item.get("local_sku")),
                        "fnsku": _to_str(item.get("fnsku")),
                        "mid": _to_str(item.get("mid")),
                        "amz_product_id": _to_str(item.get("amz_product_id")),
                        "amz_product_id_type_text": _to_str(item.get("amz_product_id_type_text")),
                        "parent_asin": _to_str(item.get("parent_asin")),
                        "product_id": _to_str(item.get("product_id")),
                        "product_relation_id": _to_str(item.get("product_relation_id")),
                        "id_hash": _to_str(item.get("id_hash")),
                        # 名称与描述
                        "product_name": product_name,
                        "listing_title": _to_str(item.get("item_name")),
                        "local_name": _to_str(item.get("local_name")),
                        "model": _to_str(item.get("model")),
                        "variant": _to_json(item.get("variant")),
                        "variant_text": _to_str(item.get("variant_text")),
                        "remark": _to_str(item.get("remark")),
                        "amz_product_type": _to_str(item.get("amz_product_type")),
                        # 价格与费用
                        # 售价 price 用当前 Listing 价（listing_price），旧值 price（与 daily_price/regular_price 相同）已按需求弃用
                        "price": _to_str(item.get("listing_price") or item.get("price")),
                        "listing_price": _to_str(item.get("listing_price")),
                        "landed_price": _to_str(item.get("landed_price")),
                        "regular_price": _to_str(item.get("regular_price")),
                        "list_price": _to_str(item.get("list_price")),
                        "b2b_price": _to_str(item.get("b2b_price")),
                        "b2b_price_discount": _to_json(item.get("b2b_price_discount")),
                        "fba_fee": _to_str(item.get("fba_fee")),
                        "report_fba_fee": _to_str(item.get("report_fba_fee")),
                        "referral_fee": _to_str(item.get("referral_fee")),
                        "shipping": _to_str(item.get("shipping")),
                        "points": _to_str(item.get("points")),
                        "history_price": _to_str(item.get("history_price")),
                        "history_price_source": _to_str(item.get("history_price_source")),
                        "currency_symbol": _to_str(item.get("currency_symbol")),
                        # 销量与销售额
                        "total_volume": _to_int(item.get("total_volume")),
                        "yesterday_volume": _to_int(item.get("yesterday_volume")),
                        "seven_volume": _to_int(item.get("seven_volume")),
                        "fourteen_volume": _to_int(item.get("fourteen_volume")),
                        "thirty_volume": _to_int(item.get("thirty_volume")),
                        "average_seven_volume": _to_float(item.get("average_seven_volume")),
                        "average_fourteen_volume": _to_float(item.get("average_fourteen_volume")),
                        "average_thirty_volume": _to_float(item.get("average_thirty_volume")),
                        "yesterday_amount": _to_float(item.get("yesterday_amount")),
                        "seven_amount": _to_float(item.get("seven_amount")),
                        "fourteen_amount": _to_float(item.get("fourteen_amount")),
                        "thirty_amount": _to_float(item.get("thirty_amount")),
                        # 广告花费
                        "yesterday_spend": _to_float(item.get("yesterday_spend")),
                        "seven_spend": _to_float(item.get("seven_spend")),
                        "fourteen_spend": _to_float(item.get("fourteen_spend")),
                        "thirty_spend": _to_float(item.get("thirty_spend")),
                        # 库存（FBA）
                        "afn_fulfillable_quantity": _to_int(item.get("afn_fulfillable_quantity")),
                        "afn_reserved_quantity": _to_int(item.get("afn_reserved_quantity")),
                        "reserved_fc_transfers": _to_int(item.get("reserved_fc_transfers")),
                        "reserved_fc_processing": _to_int(item.get("reserved_fc_processing")),
                        "reserved_customerorders": _to_int(item.get("reserved_customerorders")),
                        "afn_inbound_shipped_quantity": _to_int(item.get("afn_inbound_shipped_quantity")),
                        "afn_unsellable_quantity": _to_int(item.get("afn_unsellable_quantity")),
                        "afn_inbound_working_quantity": _to_int(item.get("afn_inbound_working_quantity")),
                        "afn_inbound_receiving_quantity": _to_int(item.get("afn_inbound_receiving_quantity")),
                        "quantity": _to_int(item.get("quantity")),
                        # 排名与表现
                        "rank": _to_int(item.get("rank")),
                        "seller_rank": _to_int(item.get("seller_rank")),
                        "category_rank": _to_json(item.get("category_rank")),
                        "small_rank": parse_small_rank(item.get("small_rank")),
                        "seller_category": _to_str(item.get("seller_category")),
                        "category_url": _to_str(item.get("category_url")),
                        "stars": _to_float(item.get("stars")),
                        "reviews_num": _to_int(item.get("reviews_num")),
                        # 时间字段
                        "open_date_time": _to_str(item.get("open_date_time")),
                        "first_order_time": _to_str(item.get("first_order_time")),
                        "first_order_type": _to_str(item.get("first_order_type")),
                        "first_order_update": _to_str(item.get("first_order_update")),
                        "on_sale_time": _to_str(item.get("on_sale_time")),
                        "create_time": _to_str(item.get("create_time")),
                        "update_time": _to_str(item.get("update_time")),
                        # 分类与品牌
                        "category_id": _to_int(item.get("category_id")),
                        "brand_id": _to_int(item.get("brand_id")),
                        "brand_name": _to_str(item.get("product_brand_text")),
                        "brand": _to_str(item.get("seller_brand")),
                        "category": category,
                        "sub_category": sub_category,
                        # 店铺与运营
                        "shop": _to_str(item.get("shop")),
                        "marketplace": _to_str(item.get("marketplace")),
                        "seller_name": _to_str(item.get("seller_name")),
                        "store_type": _to_str(item.get("store_type")),
                        "fulfillment_channel_type": _to_str(item.get("fulfillment_channel_type")),
                        "status_text": _to_str(item.get("status_text")),
                        "is_delete": _to_str(item.get("is_delete")),
                        "principal_list": _to_json(item.get("principal_list")),
                        "principal_uids": _to_json(item.get("principal_uids")),
                        "permission_user_info": _to_json(item.get("permission_user_info")),
                        "product_creator_realname": _to_str(item.get("product_creator_realname")),
                        "product_developer": _to_str(item.get("product_developer")),
                        "icon": _to_str(item.get("icon")),
                        # 标签
                        "tags": tags_map.get(asin),
                        # 节日直接按 listing 标签（具体节日优先，其次季节类）
                        "festival": festival_val,
                        # 业务
                        "list_date": parse_open_date(item.get("open_date")),
                        "product_type": product_type,
                        "status": is_active,
                        # 负责人统一清洗（去角色前缀/尾部数字/已删除标记），避免脏名
                        "operator": clean_operator_field(_to_str(item.get("principal_realname"))),
                    }
                    values = {k: v for k, v in values.items() if v is not None}
                    # 同一 ASIN 多行时（同一 ASIN 挂多个 msku）：在售优先 + 同量保大 + 列级合并
                    # 1) 在售行优先于停售/已删除行；
                    # 2) 同在售时以近30天销量大的一行为基础；
                    # 3) 基础行为 0 的数值列，取其他在售行的非 0 值合并（不丢在途等数据）。
                    #    如 elf hat(可售75/在途0/销量53) + jingle bell hat(可售0/在途521/销量0)
                    #    → 合并 可售75 在途521 销量53
                    from utils.asin_merge import merge_asin_records
                    data_map[asin] = merge_asin_records(data_map.get(asin), values) or values
                except Exception as e:
                    errors += 1
                    if errors <= 5:
                        logger.warning(f"  跳过 ASIN={asin}: {e}")

            # 按创建时间区间过滤（by_create_time 模式；配合服务端参数，客户端兜底）
            if mode == "by_create_time":
                filtered = {}
                for asin, values in data_map.items():
                    ct = erp_create_map.get(asin, "")
                    if _date_in_range(ct, create_start, create_end):
                        filtered[asin] = values
                if len(filtered) != len(data_map):
                    logger.info(f"按创建时间过滤: {len(data_map)} → {len(filtered)}（区间 {create_start or '-'} ~ {create_end or '-'}）")
                data_map = filtered

            # 排除列表：同样落库（不再从导入数据中剔除），落库后统一定为「已排除」，
            # 便于「所有ASIN列表」完整展示全量 ASIN 与现阶段状态

            # 已存在的产品 → 更新基础字段；新产品 → 插入
            asins = list(data_map.keys())
            exist = await session.execute(select(Product.asin).where(Product.asin.in_(asins)))
            exist_set = set(exist.scalars().all())
            updated = 0
            inserted = 0
            for i, (asin, values) in enumerate(data_map.items()):
                if asin in exist_set:
                    # 保留列表的 ASIN：导入不覆盖 status/status_text，保护运营人工「保留」的结果；
                    # 从保留列表移除后，下一次清洗即按领星状态重新判定
                    vals = values
                    if asin in keep:
                        vals = {k: v for k, v in values.items() if k not in ("status", "status_text")}
                    await session.execute(update(Product).where(Product.asin == asin).values(**vals))
                    updated += 1
                else:
                    if asin in keep:
                        values = {**values, "status": True, "status_text": "在售"}
                    session.add(Product(asin=asin, **values))
                    inserted += 1
                if (i + 1) % 500 == 0:
                    await session.flush()
                    logger.info(f"  已处理 {i + 1}/{len(asins)} 条...")

            # 已删除自动比对：库中已有、但最新报表不存在的产品 → 标记为已删除（保留列表除外；按创建时间模式不删除）
            deleted = 0
            if mode == "full":
                all_db = await session.execute(select(Product.asin))
                db_set = set(all_db.scalars().all())
                report_set = set(data_map.keys())
                missing = (db_set - report_set) - keep
                if missing:
                    await session.execute(
                        update(Product)
                        .where(Product.asin.in_(missing))
                        .values(status=False, status_text="已删除")
                    )
                    deleted = len(missing)

            # 排除列表：库中已存在的被排除 ASIN → 强制停用并标记
            excluded_marked = 0
            if exclude:
                await session.execute(
                    update(Product)
                    .where(Product.asin.in_(exclude))
                    .values(status=False, status_text="已排除")
                )
                excluded_marked = len(exclude)

            await session.commit()
            logger.info(f"导入完成: 更新 {updated} 条, 新增 {inserted} 条, 标记已删除 {deleted} 条, "
                        f"排除 {excluded_marked} 条, 失败 {errors} 条（mode={mode}）")
            return updated + inserted
    finally:
        await session.close()


async def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(base_dir, "p_id", "msku_id_full.json")

    if not os.path.exists(json_path):
        logger.error(f"文件不存在: {json_path}")
        return

    await import_products(json_path)


if __name__ == "__main__":
    asyncio.run(main())
