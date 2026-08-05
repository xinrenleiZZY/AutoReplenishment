# -*- coding: utf-8 -*-
"""
领星标签数据爬虫 — getRelationTagList API
流程:
  1. 全量请求 → 写入 msku_tags_raw.jsonl (原始响应, 每行一个batch)
  2. 清洗: msku_tags_raw.jsonl → tags_full.json (ASIN→tagInfos 映射)
  3. 导入数据库: tags_full.json → products.tags 字段

正确 API (2026-07 验证):
  POST https://gw.lingxingerp.com/global-tag/global/tag/relation/getRelationTagList
  body: {"bindDetail": [{"sid": store_id, "relationId": msku}, ...]}
  response.data: [{relationId, sid, sku, tagInfos: [...]}, ...]
  匹配方式: response[].relationId == msku → ASIN
"""

import requests
import json
import os
import sys
import time
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P_ID_DIR = os.path.join(BASE_DIR, "p_id")
RAW_JSONL_PATH = os.path.join(P_ID_DIR, "msku_tags_raw.jsonl")
TAGS_JSON_PATH = os.path.join(P_ID_DIR, "tags_full.json")
BATCH_SIZE = 50

API_URL = "https://gw.lingxingerp.com/global-tag/global/tag/relation/getRelationTagList"

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_auth_token():
    token = os.getenv("LX_AUTH_TOKEN", "")
    if not token:
        token = "9734U0+ydVSMrW06kQ/RdDXMCZ2gvyWT2bgoUTsSioC+2hzQmMQ0EBm54bF3TsG3A8BVXFU/sutsxCuzIFdlbKOV4Eyqm5qQC4medqn5aINFdaf+RSZgJzuy1WayIHU14lqlKGaDkY8NMghvUrptwcTyTeg"
    return token


AUTH_TOKEN = get_auth_token()

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "ak-client-type": "web",
    "ak-origin": "https://huizhixin.lingxing.com",
    "auth-token": AUTH_TOKEN,
    "content-type": "application/json;charset=UTF-8",
    "origin": "https://huizhixin.lingxing.com",
    "referer": "https://huizhixin.lingxing.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/139.0.0.0 Safari/537.36",
    "x-ak-company-id": "90136117059997696",
    "x-ak-env-key": "huizhixin",
    "x-ak-language": "zh",
    "x-ak-platform": "1",
    "x-ak-request-source": "erp",
    "x-ak-uid": "11054904",
    "x-ak-version": "3.8.8.3.0.045",
    "x-ak-zid": "1",
}


def read_all_products() -> list[dict]:
    """
    读取产品数据，返回 [{"msku": ..., "asin": ..., "store_id": ...}, ...]
    store_id 在数据中为 sid，对应 API 中的 sid 字段
    """
    json_path = os.path.join(P_ID_DIR, "msku_id_full.json")
    if not os.path.exists(json_path):
        logger.error(f"产品数据文件不存在: {json_path}")
        return []
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    products = []
    for item in data:
        msku = item.get("msku", "").strip()
        asin = item.get("asin", "").strip()
        store_id = item.get("store_id")
        if msku and asin and store_id:
            products.append({"msku": msku, "asin": asin, "store_id": store_id})

    logger.info(f"读取到 {len(products)} 个产品 (含 msku+asin+store_id)")
    # 去重（msku 可能重复）
    seen = set()
    unique = []
    for p in products:
        key = (p["msku"], p["store_id"])
        if key not in seen:
            seen.add(key)
            unique.append(p)
    if len(unique) < len(products):
        logger.info(f"去重后剩余 {len(unique)} 个产品")
    return unique


def fetch_tags(bind_detail: list, req_seq: int) -> list:
    """调用 getRelationTagList API"""
    payload = {
        "bindDetail": bind_detail,
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"API error: code={data.get('code')}, msg={data.get('msg')}")
    raw = data.get("data")
    return raw if isinstance(raw, list) else []


def scrape_all_tags():
    """Step 1: 全量请求 → 流式写入 JSONL"""
    products = read_all_products()
    if not products:
        return

    os.makedirs(P_ID_DIR, exist_ok=True)
    total = len(products)

    # 构建 bindDetail 列表
    all_bind = [{"sid": p["store_id"], "relationId": p["msku"]} for p in products]

    # 如果已有 JSONL，清空重新开始（API 无续传支持）
    f = open(RAW_JSONL_PATH, "w", encoding="utf-8")
    seq = 0
    try:
        for i in range(0, total, BATCH_SIZE):
            batch_bind = all_bind[i:i + BATCH_SIZE]
            batch_products = products[i:i + BATCH_SIZE]
            batch_mskus = [p["msku"] for p in batch_products]
            seq += 1
            try:
                resp_data = fetch_tags(batch_bind, seq)
                # 写入原始 JSONL（每行 = 该 batch 的产品列表 + 响应数据）
                line = json.dumps({"products": batch_products, "data": resp_data}, ensure_ascii=False)
                f.write(line + "\n")
                f.flush()
                logger.info(f"[{i+1}-{min(i+len(batch_products), total)}/{total}] 成功, 返回{len(resp_data)}条")
            except Exception as e:
                err = str(e)
                if "8003" in err or "鉴权" in err or "token" in err.lower():
                    logger.error(f"Token过期, 跳过 batch [{i+1}-{i+len(batch_products)}]")
                    line = json.dumps({"products": batch_products, "data": [], "error": str(e)}, ensure_ascii=False)
                    f.write(line + "\n")
                    f.flush()
                else:
                    logger.error(f"batch失败 [{i+1}-{i+len(batch_products)}]: {e}")
                    line = json.dumps({"products": batch_products, "data": [], "error": str(e)}, ensure_ascii=False)
                    f.write(line + "\n")
                    f.flush()
            time.sleep(0.3)
    finally:
        f.close()

    logger.info(f"\n原始数据写入完成 → {RAW_JSONL_PATH}")
    clean_tags()


def clean_tags():
    """Step 2: JSONL → 按 relationId(msku) 匹配 ASIN → tags_full.json"""
    if not os.path.exists(RAW_JSONL_PATH):
        logger.error(f"无原始数据: {RAW_JSONL_PATH}")
        return

    # 先建立 msku → asin 的映射
    products = read_all_products()
    msku_to_asin = {}
    for p in products:
        key = p["msku"]
        if key not in msku_to_asin:
            msku_to_asin[key] = p["asin"]

    all_tags = {}
    batch_count = 0
    tag_count = 0

    with open(RAW_JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            batch_count += 1
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("error"):
                continue

            resp_data = entry.get("data", [])
            if not isinstance(resp_data, list):
                continue

            for item in resp_data:
                msku = (item.get("relationId") or "").strip()
                tags = item.get("tagInfos", [])
                if msku and tags:
                    asin = msku_to_asin.get(msku, "")
                    if asin:
                        all_tags[asin] = tags
                        tag_count += 1
                    else:
                        logger.debug(f"msku '{msku}' 未匹配到 ASIN")

    with open(TAGS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_tags, f, ensure_ascii=False, indent=2)

    logger.info(f"\n标签清洗完成!")
    logger.info(f"  总批次: {batch_count}")
    logger.info(f"  有标签的ASIN: {len(all_tags)}")
    logger.info(f"  标签总数: {tag_count}")
    logger.info(f"  输出: {TAGS_JSON_PATH}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--clean":
        clean_tags()
    else:
        scrape_all_tags()
