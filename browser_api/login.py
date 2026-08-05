# -*- coding: utf-8 -*-
"""
领星系统登录模块（独立版）
复制自 browser/login.py，内聚所有依赖
"""
import os
import time
from dotenv import load_dotenv

from .config import LINGXING_LISTING_URL, LOG_DIR
from .logger import log_info, log_error, log_warn


class LingxingLogin:

    SELECTORS = {
        "username_input": 'input[placeholder="手机号/用户名/邮箱"]',
        "password_input": 'input[placeholder="密码"]',
        "login_btn": 'button:has-text("登录")',
        "user_menu": '[class*="avatar"], .user-info, .el-dropdown',
        "logout_btn": 'span:has-text("退出"), li:has-text("退出"), a:has-text("退出")',
    }

    def __init__(self, page, username: str = None, password: str = None):
        self.page = page
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        load_dotenv(os.path.join(_project_root, ".env"))
        self.username = username or os.getenv("LX_USERNAME", "")
        self.password = password or os.getenv("LX_PASSWORD", "")
        if not self.username or not self.password:
            log_warn("请在 .env 中设置 LX_USERNAME 和 LX_PASSWORD")

    def is_on_login_page(self) -> bool:
        """检测当前页面是否为登录页"""
        url = (self.page.url or "").lower()
        try:
            el = self.page.locator(self.SELECTORS["username_input"]).first
            if el.count() > 0 and el.is_visible():
                return True
        except Exception:
            pass
        if "/login" in url:
            return True
        if "/erp/" in url:
            return False
        return False

    def login(self) -> bool:
        """完整登录流程（一次性，不循环）"""
        log_info("开始登录领星系统...")
        self.page.goto(LINGXING_LISTING_URL, wait_until="commit", timeout=45000)
        self._wait_page_stable()

        if self._is_erp_page():
            log_info("已登录，执行退登...")
            self._do_logout()
            self._wait_for_login_page()

        if self.is_on_login_page():
            log_info("检测到登录页，执行登录...")
            self._do_login()
            self._wait_for_erp_page()
        else:
            log_error(f"未在登录页，当前 URL: {self.page.url[:100]}")
            self._screenshot("not_login_page")
            return False

        if self._is_erp_page():
            log_info("登录成功，已进入系统主页")
            return True

        self._screenshot("login_timeout")
        log_error(f"登录超时，当前 URL: {self.page.url[:100]}")
        return False

    def _is_erp_page(self) -> bool:
        url = (self.page.url or "").lower()
        if "/erp/" in url and "login" not in url:
            return True
        try:
            menu = self.page.locator(self.SELECTORS["user_menu"]).first
            if menu.count() > 0 and menu.is_visible():
                return True
        except Exception:
            pass
        return False

    def _wait_page_stable(self, timeout: int = 10):
        for _ in range(timeout):
            url = (self.page.url or "").lower()
            if "/erp/" in url or "/login" in url:
                return
            time.sleep(1)
        log_warn("页面未在预期时间内稳定")

    def _wait_for_login_page(self, timeout: int = 15):
        log_info("  等待进入登录页...")
        for _ in range(timeout):
            if self.is_on_login_page():
                log_info("  已进入登录页")
                return True
            time.sleep(1)
        log_warn("  超时未进入登录页")
        return False

    def _wait_for_erp_page(self, timeout: int = 20):
        log_info("  等待进入系统主页...")
        for _ in range(timeout):
            if self._is_erp_page():
                log_info("  已进入主页，等待页面加载...")
                time.sleep(3)
                return True
            time.sleep(1)
        log_warn("  超时未进入主页")
        return False

    def _do_logout(self):
        log_info("  退登: 点击用户菜单...")
        try:
            menu = self.page.locator(self.SELECTORS["user_menu"]).first
            if menu.count() > 0:
                menu.click()
                time.sleep(1)
        except Exception as e:
            log_warn(f"  点击用户菜单失败: {e}")

        log_info("  退登: 点击退出...")
        try:
            btn = self.page.locator(self.SELECTORS["logout_btn"]).first
            if btn.count() > 0:
                btn.click()
                time.sleep(2)
                log_info("  已退登")
                return
        except Exception as e:
            log_warn(f"  点击退出失败: {e}")

        log_info("  退登失败，直接跳转登录页...")
        self.page.goto("https://huizhixin.lingxing.com/login", wait_until="commit", timeout=30000)
        time.sleep(2)

    def _do_login(self):
        self._try_click_tab()
        self._fill_input(self.SELECTORS["username_input"], self.username)
        time.sleep(0.5)
        self._fill_input(self.SELECTORS["password_input"], self.password)
        time.sleep(0.5)
        self._click(self.SELECTORS["login_btn"])
        log_info("  已提交登录，等待跳转...")

    def _try_click_tab(self):
        try:
            tab = self.page.locator('text="账号登录"').first
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                time.sleep(0.5)
                log_info("  已点击账号登录标签")
        except Exception:
            pass

    def _fill_input(self, selector: str, value: str):
        try:
            el = self.page.locator(selector).first
            if el.count() > 0:
                el.click()
                time.sleep(0.2)
                el.fill("")
                el.fill(value)
        except Exception as e:
            log_error(f"  填写失败 ({selector}): {e}")

    def _click(self, selector: str):
        try:
            el = self.page.locator(selector).first
            if el.count() > 0 and el.is_visible():
                el.click()
        except Exception as e:
            log_error(f"  点击失败 ({selector}): {e}")

    def _screenshot(self, name: str):
        try:
            self.page.screenshot(path=os.path.join(LOG_DIR, f"{name}_{int(time.time())}.png"))
        except Exception:
            pass
