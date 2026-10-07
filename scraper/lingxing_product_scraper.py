# -*- coding: utf-8 -*-
"""
领星产品数据爬虫 — showOnline API
从 https://gw.lingxingerp.com/listing-api/api/product/showOnline 全量抓取
流式写入 msku_id_full.jsonl，然后合并为 msku_id_full.json
"""
import requests
import json
import os
import sys
import time
import subprocess
import re

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
P_ID_DIR = os.path.join(BASE_DIR, "p_id")
JSONL_PATH = os.path.join(P_ID_DIR, "msku_id_full.jsonl")
JSON_PATH = os.path.join(P_ID_DIR, "msku_id_full.json")
CLEAN_JSON_PATH = os.path.join(P_ID_DIR, "msku_id_clean_full.json")
PER_PAGE = 200

API_URL = "https://gw.lingxingerp.com/listing-api/api/product/showOnline"

# ——— auth-token 优先从 .env 读取，fallback 到硬编码 ———
from dotenv import load_dotenv
load_dotenv()

def get_auth_token():
    """获取当前 auth-token"""
    token = os.getenv("LX_AUTH_TOKEN", "")
    if not token:
        token = "9734U0+ydVSMrW06kQ/RdDXMCZ2gvyWT2bgoUTsSioC+2hzQmMQ0EBm54bF3TsG3A8BVXFU/sutsxCuzIFdlbKOV4Eyqm5qQC4medqn5aINFdaf+RSZgJzuy1WayIHU14lqlKGaDkY8NMghvUrptwcTyTeg"
    return token

def refresh_and_reload_token() -> bool:
    """运行刷新 token 逻辑：优先调用独立模块 browser_api.lingxing_auth（CDP 自动登录+捕获），
    失败时回退旧版 refresh_auth_token.py 子进程方式。

    刷新成功后自动重新加载模块内 AUTH_TOKEN / HEADERS。
    """
    token = None
    try:
        sys.path.insert(0, BASE_DIR)
        from browser_api.lingxing_auth import LingxingAuth
        cdp_port = int(os.getenv("LX_CDP_PORT", "18800"))
        auth = LingxingAuth(cdp_port=cdp_port)
        # ensure_token: 未登录则自动登录（含二次认证），再跳转listing捕获 token
        token = auth.ensure_token()
    except Exception as e:
        print(f"[WARN] 独立模块刷新 token 失败({e})，回退旧方式")
        token = None

    if not token:
        # 旧版：子进程方式（仅捕获，不自动登录）
        refresh_script = os.path.join(BASE_DIR, "tools", "refresh_auth_token.py")
        if not os.path.exists(refresh_script):
            print("[WARN] refresh_auth_token.py 不存在，无法自动刷新")
            return False
        print("[INFO] auth-token 过期，尝试自动刷新...")
        try:
            result = subprocess.run(["python", refresh_script], cwd=BASE_DIR, capture_output=True, text=True, timeout=60)
            print(result.stdout)
            if result.returncode != 0:
                print(f"[WARN] 刷新失败: {result.stderr[:300]}")
                return False
            import re as _re
            m = _re.search(r"TOKEN_OK=(.+)", result.stdout or "")
            token = m.group(1) if m else None
        except Exception as e:
            print(f"[WARN] 刷新异常: {e}")
            return False

    if token:
        global AUTH_TOKEN
        AUTH_TOKEN = token
        HEADERS["auth-token"] = AUTH_TOKEN
        return True
    return False

AUTH_TOKEN = get_auth_token()

# 清洗后保留的关键字段（来自 json需求结构.json）
KEEP_FIELDS = [
    "id", "store_id", "msku", "asin", "item_name", "status",
    "price", "fulfillment_channel_type", "marketplace_id",
    "amz_product_type", "store_type",
    "local_sku", "local_name", "category_id", "parent_asin",
    "seller_category", "seller_brand", "small_rank",
    "amz_product_id", "fnsku", "shop", "currency_symbol",
    "mid", "marketplace", "seller_name", "asin_url", "icon", "category_rank",
]

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

# 全部字段不需要过滤，直接原样写入



def fetch_page(offset: int, req_seq: int, begin_date: str = "", end_date: str = "") -> dict:
    payload = {
        "offset": offset,
        "length": PER_PAGE,
        "search_field": "msku",
        "pvi_ids": "",
        "exact_search": 1,
        "sids": "",
        "status": "",
        "is_pair": "",
        "fulfillment_channel_type": "",
        "global_tag_ids": "",
        "req_time_sequence": f"/listing-api/api/product/showOnline$${req_seq}",
    }
    # 按创建时间区间过滤（服务端参数；部分环境可能不支持，scrape_all 会回退）
    if begin_date:
        payload["begin_date"] = begin_date
    if end_date:
        payload["end_date"] = end_date

    # 首选「领星 API 服务站」：登录态由服务端注入，规避本地 auth-token 被顶号（8003/-999）
    try:
        from app.services.lx_station import station_proxy

        station_resp = station_proxy(url=API_URL, body=payload)
        if isinstance(station_resp, dict) and station_resp.get("success"):
            items = [it for it in (station_resp.get("data") or []) if isinstance(it, dict)]
            try:
                from app.services.raw_store import collect_raw

                collect_raw("showOnline_scraper", station_resp, url=API_URL, method="POST",
                            params=payload, status_code=200)
            except Exception:  # noqa: BLE001
                pass
            return {"list": items, "total": int(station_resp.get("total") or len(items))}
        print(f"[WARN] 服务站抓取未成功({str(station_resp)[:200]})，回退本地直连")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 服务站抓取失败({e})，回退本地直连")

    # 兜底：本地 auth-token 直连
    resp = requests.post(API_URL, headers=HEADERS, json=payload, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    try:
        from app.services.raw_store import collect_raw

        collect_raw("showOnline_scraper", data, url=API_URL, method="POST",
                    params=payload, status_code=resp.status_code)
    except Exception:  # noqa: BLE001
        pass
    if data.get("code") != 1:
        raise Exception(f"API error: code={data.get('code')}, msg={data.get('msg')}")
    return data.get("data", {})


def scrape_all(begin_date: str = "", end_date: str = ""):
    os.makedirs(P_ID_DIR, exist_ok=True)

    # 第一页，获取 total（鉴权失败时自动刷新 token 重试）
    requested_dates = bool(begin_date and end_date)
    effective_begin, effective_end = begin_date, end_date
    try:
        try:
            first = fetch_page(0, 1, effective_begin, effective_end)
        except Exception as e:
            if requested_dates:
                print(f"[WARN] 服务端按创建时间过滤失败({e})，回退全量抓取+客户端过滤")
                effective_begin, effective_end = "", ""
                first = fetch_page(0, 1)
            else:
                raise
    except Exception as e:
        err = str(e)
        if "8003" in err or "鉴权" in err:
            if refresh_and_reload_token():
                global AUTH_TOKEN
                AUTH_TOKEN = get_auth_token()
                HEADERS["auth-token"] = AUTH_TOKEN
                try:
                    first = fetch_page(0, 1, effective_begin, effective_end)
                except Exception:
                    effective_begin, effective_end = "", ""
                    first = fetch_page(0, 1)
            else:
                raise
        else:
            raise
    total = first.get("total", 0)
    total_pages = (total + PER_PAGE - 1) // PER_PAGE

    print(f"总数: {total}, 总页数: {total_pages}")

    # 流式写入 JSONL
    count = 0
    seq = 1
    with open(JSONL_PATH, "w", encoding="utf-8") as f:
        # 写第一页
        for item in first.get("list", []):
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
            count += 1
        print(f"offset=0, 已写入 {count} 条")

        # 写后续页
        for page in range(1, total_pages):
            time.sleep(0.3)
            offset = page * PER_PAGE
            seq += 1
            try:
                data = fetch_page(offset, seq, effective_begin, effective_end)
                items = data.get("list", [])
                for item in items:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
                    count += 1
                print(f"offset={offset}, 已写入 {count} 条")
            except Exception as e:
                print(f"  offset={offset} 异常: {e}, 跳过")
                continue

    print(f"\n完成! JSONL 写入 {count} 条 → {JSONL_PATH}")


def merge_jsonl_to_json():
    """把 msku_id_full.jsonl 合并为全量 JSON + 精简 JSON"""
    if not os.path.exists(JSONL_PATH):
        print(f"JSONL 文件不存在: {JSONL_PATH}")
        print("请先运行: python scraper/lingxing_product_scraper.py")
        return

    items = []
    clean_items = []
    with open(JSONL_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                obj = json.loads(line)
                items.append(obj)
                clean_items.append({k: obj.get(k) for k in KEEP_FIELDS if k in obj})

    # 全量 JSON
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    print(f"全量: {len(items)} 条 → {JSON_PATH}")

    # 精简 JSON
    with open(CLEAN_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(clean_items, f, ensure_ascii=False, indent=2)
    print(f"精简: {len(clean_items)} 条 → {CLEAN_JSON_PATH}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--merge":
        merge_jsonl_to_json()
    else:
        scrape_all()
        merge_jsonl_to_json()
