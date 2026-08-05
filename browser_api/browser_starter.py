# -*- coding: utf-8 -*-
"""
浏览器独立启动器（独立版）
用法: python -m browser-api.browser_starter [--port 18800]
"""
import sys
import os
import argparse
import subprocess
import socket

# 确定项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from .config import BROWSER_CDP_PORT, BROWSER_USER_DATA_DIR
from .logger import setup_logger, log_info, log_error


def check_port_in_use(port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def cleanup_zombie_processes(port: int):
    """清理占用指定端口的残留 Chrome 进程"""
    if check_port_in_use(port):
        log_info(f"端口 {port} 被占用，尝试清理...")
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, timeout=10)
                    log_info(f"  已终止 PID={pid}")
                    break
        except Exception as e:
            log_warn(f"  清理失败: {e}")


def launch_browser(port: int = None, cleanup: bool = True):
    """启动独立浏览器（使用 Playwright 异步 API 避免事件循环冲突）"""
    import asyncio
    from playwright.async_api import async_playwright

    if port is None:
        port = BROWSER_CDP_PORT

    if cleanup:
        cleanup_zombie_processes(port)

    async def _launch():
        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                channel="chrome",
                headless=False,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={port}",
                ],
            )
            log_info("使用本地 Chrome 浏览器")
        except Exception:
            log_info("本地 Chrome 不可用，回退到 Playwright 内置 Chromium")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                headless=False,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={port}",
                ],
            )

        page = await context.new_page()
        log_info(f"浏览器已启动，CDP 端口: {port}")
        log_info("请在浏览器中操作，关闭浏览器窗口即退出。")
        log_info("Ctrl+C 可安全退出（浏览器保持打开）。")

        try:
            while True:
                await asyncio.sleep(1)
                try:
                    for p in context.pages:
                        pass
                except Exception:
                    log_info("浏览器窗口已关闭，退出。")
                    break
        except KeyboardInterrupt:
            log_info("收到退出信号")

        await pw.stop()

    asyncio.run(_launch())


def main():
    parser = argparse.ArgumentParser(description="浏览器独立启动器")
    parser.add_argument("--port", type=int, default=BROWSER_CDP_PORT, help=f"CDP 端口（默认 {BROWSER_CDP_PORT}）")
    parser.add_argument("--no-cleanup", action="store_true", help="不清理残留进程")
    args = parser.parse_args()

    setup_logger()
    log_info(f"浏览器独立启动器（CDP 端口: {args.port}）")
    launch_browser(args.port, cleanup=not args.no_cleanup)


if __name__ == "__main__":
    main()
