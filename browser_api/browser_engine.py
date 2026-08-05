# -*- coding: utf-8 -*-
"""
浏览器引擎模块（独立版）
复制自 browser/browser_engine.py，内聚所有依赖
"""
import socket
import time

from playwright.sync_api import sync_playwright

from .config import (
    BROWSER_HEADLESS, BROWSER_TIMEOUT_MS, BROWSER_SLOW_MO_MS,
    BROWSER_CDP_PORT, BROWSER_USER_DATA_DIR,
)
from .logger import log_info, log_error


class BrowserEngine:
    """浏览器引擎"""

    def __init__(self, headless: bool = None, timeout_ms: int = None, slow_mo: int = None):
        self.headless = headless if headless is not None else BROWSER_HEADLESS
        self.timeout_ms = timeout_ms or BROWSER_TIMEOUT_MS
        self.slow_mo = slow_mo or BROWSER_SLOW_MO_MS
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._is_persistent = False

    def launch(self) -> 'BrowserEngine':
        """直接启动 Chromium 浏览器"""
        log_info("启动 Playwright Chromium 浏览器...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(
            headless=self.headless,
            slow_mo=self.slow_mo,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
            ],
        )
        self.context = self.browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        log_info(f"浏览器已启动（headless={self.headless}）")
        return self

    def launch_persistent(self, cdp_port: int = None) -> 'BrowserEngine':
        """独立持久化浏览器模式，开放 CDP 端口"""
        if cdp_port is None:
            cdp_port = BROWSER_CDP_PORT

        log_info(f"启动持久化 Chrome 浏览器（CDP 端口: {cdp_port}）...")
        log_info(f"用户数据目录: {BROWSER_USER_DATA_DIR}")

        self.playwright = sync_playwright().start()
        self._is_persistent = True

        try:
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                channel="chrome",
                headless=self.headless,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={cdp_port}",
                ],
            )
            log_info("使用本地 Chrome 浏览器")
        except Exception:
            log_info("本地 Chrome 不可用，回退到 Playwright 内置 Chromium")
            self.context = self.playwright.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                headless=self.headless,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={cdp_port}",
                ],
            )

        self.page = self.context.new_page()
        self.page.set_default_timeout(self.timeout_ms)
        log_info(f"持久化浏览器已启动，CDP: http://127.0.0.1:{cdp_port}")
        return self

    def connect_cdp(self, cdp_url: str = None) -> 'BrowserEngine':
        """通过 CDP 协议连接到已有的浏览器实例"""
        if cdp_url is None:
            cdp_url = f"http://127.0.0.1:{BROWSER_CDP_PORT}"
        log_info(f"通过 CDP 连接浏览器: {cdp_url}")
        self.playwright = sync_playwright().start()
        self._connected_via_cdp = True
        self.browser = self.playwright.chromium.connect_over_cdp(cdp_url)

        self.page = None
        for ctx in self.browser.contexts:
            for p in ctx.pages:
                url = p.url or ""
                if "erp" in url or "huizhixin" in url:
                    self.context = ctx
                    self.page = p
                    break
            if self.page:
                break

        if self.page is None:
            if self.browser.contexts:
                self.context = self.browser.contexts[0]
                self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            else:
                self.context = self.browser.new_context()
                self.page = self.context.new_page()

        self.page.set_default_timeout(self.timeout_ms)
        log_info(f"CDP 连接成功, 当前页面: {self.page.url[:80]}")
        return self

    @staticmethod
    def is_cdp_available(port: int = None) -> bool:
        if port is None:
            port = BROWSER_CDP_PORT
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            result = sock.connect_ex(('127.0.0.1', port))
            sock.close()
            return result == 0
        except Exception:
            return False

    @staticmethod
    def auto_connect_or_launch(cdp_port: int = None, headless: bool = None) -> 'BrowserEngine':
        if cdp_port is None:
            cdp_port = BROWSER_CDP_PORT
        engine = BrowserEngine(headless=headless)
        if BrowserEngine.is_cdp_available(cdp_port):
            log_info(f"检测到端口 {cdp_port} 已有浏览器，自动连接")
            engine.connect_cdp(f"http://127.0.0.1:{cdp_port}")
        else:
            log_info(f"端口 {cdp_port} 无浏览器，启动持久化浏览器")
            engine.launch_persistent(cdp_port)
        return engine

    def navigate(self, url: str):
        log_info(f"导航到: {url}")
        self.page.goto(url, wait_until="networkidle")
        log_info(f"页面加载完成: {self.page.title()}")

    def close(self, force_close: bool = False):
        if self._is_persistent and not force_close:
            log_info("持久化模式：保持浏览器运行，仅断开 Playwright 连接")
            try:
                if self.playwright:
                    self.playwright.stop()
            except Exception as e:
                log_error(f"断开 Playwright 异常: {e}")
            return

        if hasattr(self, '_connected_via_cdp') and self._connected_via_cdp and not force_close:
            log_info("CDP 模式：保持浏览器运行，仅断开连接")
            try:
                if self.playwright:
                    self.playwright.stop()
            except Exception as e:
                log_error(f"断开 CDP 异常: {e}")
            return

        try:
            if self.context:
                self.context.close()
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            log_error(f"关闭浏览器异常: {e}")
        log_info("浏览器已关闭")
