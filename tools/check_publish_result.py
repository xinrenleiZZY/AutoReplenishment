# -*- coding: utf-8 -*-
"""
领星刊登 API 查询脚本
通过 SKU 查询刊登结果状态

用法:
    python tools/check_publish_result.py --sku B0H1BYW6XB
    python tools/check_publish_result.py --sku B0XXXXX --cdp http://127.0.0.1:18800  (从浏览器拿 cookie)

API: POST https://huizhixin.lingxing.com/listing/publish/openapi/amazon/product/list
"""
import sys
import os
import json
import argparse
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.logger import setup_logger, log_info, log_error, log_warn

# 领星 OpenAPI 需要 API 账号，默认从浏览器拿出来的 cookie 可能不够
# 如果没有 API 账号，先尝试走浏览器 cookie 方式

API_BASE = "https://huizhixin.lingxing.com"
API_PATH = "/listing/publish/openapi/amazon/product/list"


def get_cookies_from_cdp(cdp_url: str) -> dict:
    """从 CDP 浏览器获取当前页面的所有 cookie"""
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        page = browser.contexts[0].pages[0]
        cookies = page.context.cookies()
        return {c["name"]: c["value"] for c in cookies}
    finally:
        pw.stop()


def query_publish_by_sku(sku: str, cookies: dict = None, api_token: str = None) -> dict:
    """
    查询刊登结果

    参数:
        sku: SKU
        cookies: 浏览器 cookies（可选）
        api_token: API Token（可选，优先级最高）

    返回: API 原始响应 JSON
    """
    url = f"{API_BASE}{API_PATH}"
    payload = {
        "sku": sku,
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
    }

    # 如果有 API token 走 token 鉴权
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    elif cookies:
        # 走 cookie 鉴权
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers["Cookie"] = cookie_str

    resp = requests.post(url, json=payload, headers=headers, timeout=30)
    log_info(f"HTTP {resp.status_code}")

    try:
        data = resp.json()
        return data
    except Exception:
        log_error(f"响应不是 JSON: {resp.text[:500]}")
        return {"error": resp.text[:500]}


def parse_result(data: dict) -> str:
    """解析 API 返回结果，返回可读状态"""
    if data.get("code") != 1:
        return f"API 返回错误: code={data.get('code')}, msg={data.get('msg')}"

    items = data.get("data", [])
    if not items:
        return "未查询到结果"

    lines = []
    for item in items:
        sku = item.get("sku", "?")
        status_map = {0: "处理中", 1: "成功", 2: "失败"}
        status = status_map.get(item.get("status", -1), f"未知({item.get('status')})")
        reason = item.get("failure_reason", "")
        finish_time = item.get("finish_time", "")
        op_time = item.get("operate_time", "")

        lines.append(f"SKU: {sku}")
        lines.append(f"  状态: {status}")
        if reason:
            lines.append(f"  失败原因: {reason}")
        lines.append(f"  操作时间: {op_time}")
        lines.append(f"  完成时间: {finish_time}")
        lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="领星刊登结果查询")
    parser.add_argument("--sku", type=str, required=True, help="SKU")
    parser.add_argument("--cdp", default="http://127.0.0.1:18800", help="CDP地址（从浏览器拿cookie）")
    parser.add_argument("--token", type=str, default=None, help="API Token（如果有）")
    args = parser.parse_args()

    setup_logger()

    cookies = None
    if args.token:
        log_info("使用 API Token 方式")
    else:
        log_info(f"从 CDP 获取 cookies: {args.cdp}")
        try:
            cookies = get_cookies_from_cdp(args.cdp)
            log_info(f"获取到 {len(cookies)} 个 cookies")
        except Exception as e:
            log_error(f"获取 cookies 失败: {e}")
            log_info("尝试不用 cookie 直接请求...")

    log_info(f"查询 SKU: {args.sku}")
    data = query_publish_by_sku(args.sku, cookies=cookies, api_token=args.token)
    print("\n" + "=" * 60)
    print(parse_result(data))
    print("=" * 60)

    # 打印原始 JSON（调试用）
    log_info("\n原始返回:")
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
