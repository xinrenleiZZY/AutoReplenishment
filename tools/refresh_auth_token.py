# -*- coding: utf-8 -*-
"""
自动从浏览器获取领星 auth-token 并写入 .env

原理：通过 CDP 连接浏览器，拦截 showOnline 请求的 auth-token header。

用法:
    python tools/refresh_auth_token.py
    python tools/refresh_auth_token.py --cdp-port 18800
"""
import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.logger import setup_logger, log_info, log_error, log_warn

ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
LISTING_URL = "https://huizhixin.lingxing.com/erp/listing"


def _update_env_var(env_path: str, key: str, value: str):
    """更新 .env 中的指定变量"""
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")
        return

    lines = []
    found = False
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped.startswith(f"{key}=") or stripped.startswith(f"# {key}"):
                lines.append(f"{key}={value}\n")
                found = True
            else:
                lines.append(line)

    if not found:
        lines.append(f"\n{key}={value}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main():
    parser = argparse.ArgumentParser(description="自动获取领星 auth-token")
    parser.add_argument("--cdp-port", type=int, default=18800, help="CDP 端口")
    args = parser.parse_args()

    setup_logger()

    captured_token = {"value": None}

    def on_request(request):
        url = request.url
        if "showOnline" in url:
            try:
                token = request.headers.get("auth-token", "")
            except Exception:
                token = ""
            if not token:
                try:
                    token = (request.all_headers().get("auth-token", "") or
                             request.all_headers().get("Auth-Token", ""))
                except Exception:
                    pass
            if token:
                log_info(f"捕获到 auth-token: {token[:20]}...{token[-10:]}")
                captured_token["value"] = token

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{args.cdp_port}")
    except Exception as e:
        log_error(f"CDP 连接失败: {e}")
        log_info("请先启动浏览器: python browser/browser_starter.py")
        pw.stop()
        return

    # 找 ERP 页面
    page = None
    for ctx in browser.contexts:
        for p in ctx.pages:
            if "huizhixin" in (p.url or ""):
                page = p
                break
        if page:
            break
    if page is None:
        page = browser.contexts[0].pages[0] if browser.contexts[0].pages else browser.contexts[0].new_page()

    log_info(f"当前页面: {page.url[:100]}")

    # 监听请求
    page.on("request", on_request)

    # 导航到 listing 页面
    log_info("导航到 listing 页面以触发 API 请求...")
    try:
        page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=30000)
    except Exception:
        log_warn("导航超时，但仍会尝试捕获请求...")
    time.sleep(2)

    # 触发搜索以强制发出 showOnline 请求
    log_info("触发搜索以强制发出 API 请求...")
    try:
        si = page.locator('input[type="text"]').first
        if si.count() > 0 and si.is_visible():
            si.click()
            time.sleep(0.3)
            si.fill("B0")
            si.press("Enter")
            log_info("  已触发搜索")
    except Exception:
        log_warn("  搜索触发失败")

    # 等待 API 请求
    log_info("等待捕获 showOnline 请求...")
    for i in range(10):
        if captured_token["value"]:
            break
        time.sleep(1)

    pw.stop()

    if captured_token["value"]:
        _update_env_var(ENV_PATH, "LX_AUTH_TOKEN", captured_token["value"])
        log_info(f"auth-token 已写入 .env → LX_AUTH_TOKEN")
        log_info("请同步更新 scraper 代码以读取 .env")
    else:
        log_error("未捕获到 auth-token")
        log_info("请确保浏览器已登录领星，且 listing 页面的 showOnline 请求已发出")


if __name__ == "__main__":
    main()
