# -*- coding: utf-8 -*-
"""
领星箱规（单箱数量）爬虫 — product/lists API
用法:
  python scraper/lingxing_box_qty_scraper.py          # 全量抓取+清洗
  python scraper/lingxing_box_qty_scraper.py --clean   # 仅清洗
  python scraper/lingxing_box_qty_scraper.py --import  # 仅导入数据库
"""

import requests
import json
import os
import sys
import time
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P_ID_DIR = os.path.join(BASE_DIR, "p_id")
RAW_JSONL_PATH = os.path.join(P_ID_DIR, "box_qty_raw.jsonl")
BOX_QTY_JSON_PATH = os.path.join(P_ID_DIR, "box_qty_full.json")
MSKU_JSON_PATH = os.path.join(P_ID_DIR, "msku_id_full.json")

API_URL = "https://huizhixin.lingxing.com/api/product/lists"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _get_headers():
    """每次请求动态构造 headers"""
    token = "290ekBXwbZdWjjXJ9GDr5NoVRLnCxFkT1/KF2a2+Xlgo+GlNHZeQneFq169O8AOLjrGM6E2NZScHPaTg53PSFP+mm01jrQ0Cj+blF0EknRw3ek7mvyJT8LwGxNzbRgkpmaKwtIObNh6MsWBCGXbdvVgj+hY"

    return {
        "accept": "application/json, text/plain, */*",
        "ak-client-type": "web",
        "ak-origin": "https://huizhixin.lingxing.com",
        "auth-token": token,
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://huizhixin.lingxing.com",
        "referer": "https://huizhixin.lingxing.com/erp/productManage",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "x-ak-company-id": "90136117059997696",
        "x-ak-env-key": "huizhixin",
        "x-ak-language": "zh",
        "x-ak-platform": "1",
        "x-ak-request-source": "erp",
        "x-ak-uid": "11054904",
        "x-ak-version": "3.8.8.3.0.105",
        "x-ak-zid": "1",
    }


def load_msku_map() -> dict:
    """加载 msku -> ASIN 映射"""
    if not os.path.exists(MSKU_JSON_PATH):
        logger.error(f"产品映射文件不存在: {MSKU_JSON_PATH}")
        return {}
    with open(MSKU_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    mapping = {}
    for item in data:
        msku = (item.get("msku") or "").strip()
        asin = (item.get("asin") or "").strip()
        if msku and asin:
            mapping[msku] = asin
    logger.info(f"加载 msku->ASIN 映射: {len(mapping)} 条")
    return mapping


def fetch_page(offset: int, length: int = 500) -> dict:
    """调用 product/lists API
    注意:
      1. payload 不能带多余空字段（search_field/status等），否则服务端返回 total=0
      2. 分页参数是 offset/length（page/page_size 被服务端忽略，会重复返回第一页）
    """
    payload = {
        "offset": offset,
        "length": length,
    }
    resp = requests.post(API_URL, headers=_get_headers(), json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"API error: code={data.get('code')}, msg={data.get('msg')}")
    return data


def scrape_all():
    """全量分页请求 -> JSONL"""
    os.makedirs(P_ID_DIR, exist_ok=True)

    first = fetch_page(0)
    total = first.get("total", 0)
    items_first = first.get("list", [])
    per_page = len(items_first) if items_first else 500
    total_pages = (total + per_page - 1) // per_page
    logger.info(f"总数: {total}, 每页: {per_page}, 总页数: {total_pages}")

    count = 0
    with open(RAW_JSONL_PATH, "w", encoding="utf-8") as f:
        for item in items_first:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
        logger.info(f"offset=0, 已写入 {count} 条")

        for p in range(1, total_pages):
            time.sleep(0.3)
            offset = p * per_page
            try:
                data = fetch_page(offset)
                items = data.get("list", [])
                for item in items:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    count += 1
                logger.info(f"offset={offset}, 已写入 {count} 条")
            except Exception as e:
                logger.error(f"offset={offset} 异常: {e}, 跳过")

    logger.info(f"抓取完成! {count} 条 -> {RAW_JSONL_PATH}")
    clean_box_qty()


def clean_box_qty():
    """JSONL -> ASIN: cg_box_pcs"""
    if not os.path.exists(RAW_JSONL_PATH):
        logger.error(f"无原始数据: {RAW_JSONL_PATH}")
        return

    msku_map = load_msku_map()
    if not msku_map:
        return

    result = {}
    combo_matched = 0

    with open(RAW_JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            sku = (item.get("sku") or "").strip()
            box_pcs = item.get("cg_box_pcs", 0)
            if not isinstance(box_pcs, int):
                box_pcs = int(box_pcs) if box_pcs else 0

            asin = msku_map.get(sku, "")
            if not asin and item.get("is_combo") == 1:
                sons = item.get("sonProducts") or []
                if sons:
                    son_sku = (sons[0].get("sku") or "").strip()
                    asin = msku_map.get(son_sku, "")
                    if asin:
                        combo_matched += 1

            if not asin:
                continue

            if asin in result:
                if box_pcs > 0 and result[asin] == 0:
                    result[asin] = box_pcs
            else:
                result[asin] = box_pcs

    with open(BOX_QTY_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    nonzero = sum(1 for v in result.values() if v > 0)
    logger.info(f"箱规清洗完成!")
    logger.info(f"  匹配 ASIN: {len(result)}")
    logger.info(f"  cg_box_pcs>0: {nonzero}")
    logger.info(f"  组合产品匹配: {combo_matched}")
    logger.info(f"  输出: {BOX_QTY_JSON_PATH}")


async def run_sync():
    """定时任务完整流程: 抓取 → 清洗 → 导入数据库（供 scheduler 每日调用）

    与 ASIN 基础信息同步一致，归入每日定时爬取维度。
    cg_box_pcs=0 属于正常（ERP 未填写），只更新 >0 的产品。
    """
    scrape_all()          # 抓取 + 清洗（同步，内部已调用 clean_box_qty）
    import_to_db()        # 导入数据库


def import_to_db():
    """box_qty_full.json -> products.box_quantity"""
    if not os.path.exists(BOX_QTY_JSON_PATH):
        logger.error(f"箱规数据不存在: {BOX_QTY_JSON_PATH}")
        return

    with open(BOX_QTY_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    import asyncio
    asyncio.run(_async_import(data))


async def _async_import(data: dict):
    sys.path.insert(0, BASE_DIR)
    from sqlalchemy import update
    from app.database import async_session_factory
    from app.models.product import Product

    session = async_session_factory()
    try:
        async with session:
            updated = 0
            for asin, box_pcs in data.items():
                if box_pcs <= 0:
                    continue
                await session.execute(
                    update(Product)
                    .where(Product.asin == asin)
                    .values(box_quantity=box_pcs)
                )
                updated += 1
            await session.commit()
            logger.info(f"数据库导入完成: {updated} 条 box_quantity 已更新")
    finally:
        await session.close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--clean":
            clean_box_qty()
        elif sys.argv[1] == "--import":
            import_to_db()
        else:
            print(f"用法: python {sys.argv[0]} [--clean|--import]")
    else:
        scrape_all()
