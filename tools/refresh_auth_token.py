# -*- coding: utf-8 -*-
"""
自动获取领星 auth-token 并写入 .env（独立模块 lingxing_auth 的命令行封装）

用法:
    python tools/refresh_auth_token.py                 # 确保登录并获取 token
    python tools/refresh_auth_token.py --cdp-port 18800
    python tools/refresh_auth_token.py --login         # 仅自动登录（含二次认证）
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from browser_api.lingxing_auth import LingxingAuth


def main():
    parser = argparse.ArgumentParser(description="自动获取领星 auth-token")
    parser.add_argument("--cdp-port", type=int, default=None, help="CDP 端口（默认 18800）")
    parser.add_argument("--login", action="store_true", help="仅执行自动登录（含二次认证）")
    args = parser.parse_args()

    auth = LingxingAuth(cdp_port=args.cdp_port)
    if args.login:
        ok = auth.ensure_login()
        print("LOGIN_OK" if ok else "LOGIN_FAILED")
        return

    token = auth.ensure_token()
    if token:
        print(f"TOKEN_OK={token[:20]}...{token[-10:]}")
    else:
        print("TOKEN_FAILED")


if __name__ == "__main__":
    main()
