# -*- coding: utf-8 -*-
"""
领星浏览器登录脚本

用途：打开领星ERP页面并登录，为 token 刷新做好准备。
前提：需在 .env 中配置 LX_USERNAME 和 LX_PASSWORD

用法:
    python scripts/lingxing_login.py              # 启动新浏览器并登录
    python scripts/lingxing_login.py --cdp-port 18800  # 连接已有CDP浏览器

登录后，浏览器保持打开，另开终端运行:
    python tools/refresh_auth_token.py
"""
import os
import sys
import time
import argparse

# 加入项目根目录到 sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from browser_api.browser_engine import BrowserEngine
from browser_api.login import LingxingLogin
from browser_api.logger import setup_logger, log_info, log_error
from browser_api.config import LINGXING_LISTING_URL, BROWSER_CDP_PORT


def main():
    parser = argparse.ArgumentParser(description="领星浏览器登录")
    parser.add_argument("--cdp-port", type=int, default=BROWSER_CDP_PORT,
                        help=f"CDP 端口（默认 {BROWSER_CDP_PORT}）")
    parser.add_argument("--launch-only", action="store_true",
                        help="仅启动浏览器，不自动登录")
    args = parser.parse_args()

    setup_logger()
    log_info("=" * 50)
    log_info("领星浏览器登录脚本启动")
    log_info("=" * 50)

    # 启动或连接浏览器
    engine = BrowserEngine.auto_connect_or_launch(cdp_port=args.cdp_port)

    if args.launch_only:
        log_info("仅启动模式，浏览器已就绪，请手动打开领星页面并登录")
        log_info("打开后访问: " + LINGXING_LISTING_URL)
        try:
            while True:
                time.sleep(5)
        except KeyboardInterrupt:
            log_info("退出")
        return

    # 执行登录
    login = LingxingLogin(engine.page)
    success = login.login()

    if success:
        log_info("✅ 领星登录成功!")
        log_info("浏览器已保持打开，现在可以另开终端运行:")
        log_info("  python tools/refresh_auth_token.py")
    else:
        log_error("❌ 登录失败，请检查 .env 中的 LX_USERNAME / LX_PASSWORD")
        log_info("浏览器保持打开，可手动登录")

    # 保持浏览器打开
    try:
        while True:
            time.sleep(5)
    except KeyboardInterrupt:
        log_info("收到退出信号，关闭连接")
        engine.close()


if __name__ == "__main__":
    main()
