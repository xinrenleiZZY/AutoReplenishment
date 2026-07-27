# -*- coding: utf-8 -*-
"""
领星标签数据爬虫 — getAdjustPricing API
从 https://gw.lingxingerp.com/listing-api/api/product/getAdjustPricing 获取产品标签(tagInfos)
返回数据格式:
{
    "tagInfos": [
        {"globalTagId": "...", "tagName": "夏季类", "color": "#88909E", "relationTime": "2025-04-25 13:56:22"},
        ...
    ]
}
输出: p_id/tags_full.json (全量 ASIN→标签 映射)
"""

import requests
import json
import os
import sys
import time
import subprocess
import logging

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
P_ID_DIR = os.path.join(BASE_DIR, "p_id")
TAGS_JSON_PATH = os.path.join(P_ID_DIR, "tags_full.json")
BATCH_SIZE = 50

API_URL = "https://gw.lingxingerp.com/listing-api/api/product/getAdjustPricing"

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def get_auth_token():
    token = os.getenv("LX_AUTH_TOKEN", "")
    if not token:
        token = "9734U0+ydVSMrW06kQ/RdDXMCZ2gvyWT2bgoUTsSioC+2hzQmMQ0EBm54bF3TsG3A8BVXFU/sutsxCuzIFdlbKOV4Eyqm5qQC4medqn5aINFdaf+RSZgJzuy1WayIHU14lqlKGaDkY8NMghvUrptwcTyTeg"
    return token


def refresh_and_reload_token() -> bool:
    refresh_script = os.path.join(BASE_DIR, "tools", "refresh_auth_token.py")
    if not os.path.exists(refresh_script):
        logger.warning("refresh_auth_token.py 不存在")
        return False
    try:
        result = subprocess.run(["python", refresh_script], cwd=BASE_DIR,
                                capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning(f"刷新失败: {result.stderr[:300]}")
            return False
        return True
    except Exception as e:
        logger.warning(f"刷新异常: {e}")
        return False


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
    "x-ak-version": "3.8.7.3.0.148",
    "x-ak-zid": "1",
}


def read_all_asins() -> list:
    """从产品全量JSON中读取所有ASIN"""
    json_path = os.path.join(P_ID_DIR, "msku_id_full.json")
    if not os.path.exists(json_path):
        logger.error(f"产品数据文件不存在: {json_path}")
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    asins = [item["asin"] for item in data if item.get("asin")]
    logger.info(f"读取到 {len(asins)} 个ASIN")
    return asins


def fetch_tags(pid_list: list, req_seq: int) -> dict:
    """批量获取ASIN的标签数据"""
    payload = {
        "pid_list": pid_list,
        "pvi_ids": "",
        "req_time_sequence": f"/listing-api/api/product/getAdjustPricing${req_seq}",
    }
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"API error: code={data.get('code')}, msg={data.get('msg')}")
    return data.get("data", {})


def scrape_all_tags():
    """全量抓取所有产品的标签"""
    asins = read_all_asins()
    if not asins:
        return

    os.makedirs(P_ID_DIR, exist_ok=True)
    all_tags = {}  # asin -> tagInfos

    total = len(asins)
    seq = 0
    failed_asins = []

    for i in range(0, total, BATCH_SIZE):
        batch = asins[i:i + BATCH_SIZE]
        seq += 1

        try:
            data = fetch_tags(batch, seq)
            # 返回格式: { "B0XXX": {"tagInfos": [...]}, "B0YYY": {"tagInfos": [...]} }
            for asin, tag_data in data.items():
                if isinstance(tag_data, dict) and "tagInfos" in tag_data:
                    all_tags[asin] = tag_data["tagInfos"]
                else:
                    all_tags[asin] = []

            logger.info(f"[{i+1}-{i+len(batch)}/{total}] 成功, 累计 {len(all_tags)} 个ASIN有标签")

        except Exception as e:
            err = str(e)
            if "8003" in err or "鉴权" in err:
                logger.warning("Token过期，尝试刷新...")
                if refresh_and_reload_token():
                    global AUTH_TOKEN
                    AUTH_TOKEN = get_auth_token()
                    HEADERS["auth-token"] = AUTH_TOKEN
                    try:
                        data = fetch_tags(batch, seq)
                        for asin, tag_data in data.items():
                            if isinstance(tag_data, dict) and "tagInfos" in tag_data:
                                all_tags[asin] = tag_data["tagInfos"]
                        logger.info(f"  刷新后重试成功 [{i+1}-{i+len(batch)}]")
                        continue
                    except Exception as e2:
                        logger.error(f"  刷新后仍然失败: {e2}")
                else:
                    logger.error(f"  无法刷新token: {e}")
            else:
                logger.error(f"  batch失败 [{i+1}-{i+len(batch)}]: {e}")

            failed_asins.extend(batch)
            time.sleep(1)

        time.sleep(0.3)

    # 保存结果
    with open(TAGS_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(all_tags, f, ensure_ascii=False, indent=2)

    logger.info(f"\n标签抓取完成!")
    logger.info(f"  总ASIN: {total}")
    logger.info(f"  有标签: {len(all_tags)}")
    logger.info(f"  失败的ASIN: {len(failed_asins)}")
    logger.info(f"  输出: {TAGS_JSON_PATH}")


if __name__ == "__main__":
    scrape_all_tags()
