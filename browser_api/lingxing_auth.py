# -*- coding: utf-8 -*-
"""
领星登录态独立模块（可移植）

功能：
  1. LingxingAuth.ensure_login()  — CDP 接入浏览器(默认18800端口)，若未登录则自动登录（账号密码+二次认证）
  2. LingxingAuth.fetch_auth_token() — 跳转到 Listing 页面，捕获 showOnline 请求的 auth-token，写回 .env
  3. LingxingAuth.ensure_token()  — 确保登录并返回有效 auth-token（同步失败自动重登）

设计要点：
  - 所有方法通过 CDP 连接已打开的浏览器（无需自行启动浏览器），方便复用现有登录态
  - 独立于项目其他模块，可单独运行/导入，便于移植到其他项目
  - 供项目任意数据抓取失败(鉴权失败)时调用，恢复登录态后自动重试

命令行用法：
  python -m browser_api.lingxing_auth --login           # 自动登录（若已登录则跳过）
  python -m browser_api.lingxing_auth --fetch-token     # 捕获 auth-token 写入 .env
  python -m browser_api.lingxing_auth --ensure-token    # 确保登录并返回 token（无有效登录则自动登录）

代码调用：
  from browser_api.lingxing_auth import LingxingAuth
  auth = LingxingAuth(cdp_port=18800)
  auth.ensure_login()          # 同步阻塞直到登录完成
  token = auth.fetch_auth_token()   # 返回 token 并写回 .env
  token = auth.ensure_token()       # 合并：确保登录 + 拿 token
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .config import LINGXING_LISTING_URL, LINGXING_BASE_URL, BROWSER_CDP_PORT
from .logger import log_info, log_error, log_warn, setup_logger


class LingxingAuth:
    """领星登录态管理（CDP 接入浏览器）"""

    # 登录页元素定位（CSS 选择器，对应网页前端结构）
    LOGIN_URL = f"{LINGXING_BASE_URL}/login"
    SELECTORS = {
        # 账号输入框（用户名/手机号/邮箱）——精确路径 + placeholder 兜底
        "username_input": "#app > div > div.center > div > div.right-container > div.loginForm > form > div:nth-child(1) > div > div > input",
        "username_input_fb": 'input[placeholder="手机号/用户名/邮箱"]',
        # 密码输入框
        "password_input": "#app > div > div.center > div > div.right-container > div.loginForm > form > div.el-form-item.is-success.is-required.el-form-item--large.last-form-item > div > div > input",
        "password_input_fb": 'input[placeholder="密码"]',
        # 登录按钮
        "login_btn": "#app > div > div.center > div > div.right-container > div.loginForm > form > div.el-form-item.login-item.el-form-item--large > div > button",
        "login_btn_fb": 'button:has-text("登录")',
        # 二次认证之一：跳过按钮
        "verify_skip_btn": "#reset-password > div.content > div.ak-text-center > button > span",
        # 二次认证之二：完成登录按钮
        "verify_done_btn": "#reset-password > div.content > button",
        # 账号登录标签（若登录页默认其他方式）
        "account_tab": 'text="账号登录"',
    }

    def __init__(self, cdp_port: int = None, username: str = None, password: str = None,
                 env_path: str = None, cdp_host: str = None):
        """初始化。

        Args:
            cdp_port: 浏览器 CDP 调试端口（默认取 config.BROWSER_CDP_PORT=18800）
            username: 领星账号（默认读 .env 的 LX_USERNAME）
            password: 领星密码（默认读 .env 的 LX_PASSWORD）
            env_path: .env 文件路径（默认项目根目录 .env）
            cdp_host: CDP 主机（默认 127.0.0.1；Docker 容器内访问宿主机浏览器可传 host.docker.internal）
        """
        setup_logger()
        self.cdp_port = cdp_port or BROWSER_CDP_PORT
        self.cdp_host = cdp_host or os.getenv("LX_CDP_HOST", "127.0.0.1")
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.env_path = env_path or os.path.join(self.project_root, ".env")

        # 加载 .env（确保读取到账号密码）
        try:
            from dotenv import load_dotenv
            load_dotenv(self.env_path)
        except ImportError:
            pass

        self.username = username or os.getenv("LX_USERNAME", "")
        self.password = password or os.getenv("LX_PASSWORD", "")
        if not self.username or not self.password:
            log_warn("未配置领星账号/密码（LX_USERNAME / LX_PASSWORD），自动登录将失败")

    # ────────────────────────────── CDP 连接 ──────────────────────────────

    def _connect(self):
        """通过 CDP 连接已打开的浏览器，返回 (playwright, browser, page 列表)。

        浏览器须已通过 browser_starter 启动（--remote-debugging-port=CDP端口）。
        """
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        try:
            browser = pw.chromium.connect_over_cdp(f"http://{self.cdp_host}:{self.cdp_port}")
        except Exception as e:
            pw.stop()
            raise ConnectionError(f"CDP 连接失败({self.cdp_host}:{self.cdp_port})，请先启动浏览器: python -m browser_api.browser_starter --port {self.cdp_port}。原因: {e}")
        return pw, browser

    @staticmethod
    def _get_erp_page(browser):
        """找一个领星 ERP 页面；没有则新建一个 tab 并跳转"""
        pages = []
        for ctx in browser.contexts:
            for p in ctx.pages:
                pages.append(p)
        target = None
        for p in pages:
            if "lingxing.com" in (p.url or "") and "login" not in (p.url or ""):
                target = p
                break
        if target is None:
            target = pages[0] if pages else browser.contexts[0].new_page()
        return target

    # ────────────────────────────── 登录状态判断 ──────────────────────────────

    def is_logged_in(self, page) -> bool:
        """判断当前浏览器是否已登录领星（能进入 ERP 页面即视为已登录）"""
        try:
            page.goto(LINGXING_LISTING_URL, wait_until="commit", timeout=20000)
        except Exception:
            pass
        for _ in range(10):
            url = (page.url or "").lower()
            if "login" in url and "erp" not in url:
                return False
            if "/erp/" in url:
                return True
            time.sleep(1)
        return False

    # ────────────────────────────── 自动登录（账号密码+二次认证） ──────────────────────────────

    def ensure_login(self) -> bool:
        """确保已登录领星；未登录则执行自动登录（含二次认证），返回是否登录成功。"""
        pw, browser = self._connect()
        try:
            page = self._get_erp_page(browser)
            if self.is_logged_in(page):
                log_info("已登录领星，无需重复登录")
                return True
            return self._do_login(browser, page)
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    def _do_login(self, browser, page) -> bool:
        """执行完整登录流程：跳登录页 → 填账号密码 → 提交 → 处理二次认证"""
        log_info("未登录，开始自动登录...")
        try:
            page.goto(self.LOGIN_URL, wait_until="commit", timeout=30000)
        except Exception:
            pass
        time.sleep(2)

        # 若登录页默认非“账号登录”，先切换标签
        self._try_click_account_tab(page)

        ok = self._fill_and_submit(page)
        if not ok:
            log_error("填写账号密码失败")
            return False

        # 等待二次认证或直接进入 ERP
        return self._handle_verify(page)

    def _try_click_account_tab(self, page):
        try:
            tab = page.locator(self.SELECTORS["account_tab"]).first
            if tab.count() > 0 and tab.is_visible():
                tab.click()
                time.sleep(0.8)
                log_info("已切换账号登录标签")
        except Exception:
            pass

    def _fill_and_submit(self, page) -> bool:
        """填充账号密码并点击登录，返回是否成功提交"""
        def _locator(primary_key: str, fb_key: str):
            """精确路径优先，失败回退到 placeholder 选择器"""
            el = page.locator(self.SELECTORS[primary_key]).first
            if el.count() > 0 and el.is_visible():
                return el
            fb = page.locator(self.SELECTORS[fb_key]).first
            return fb if fb.count() > 0 and fb.is_visible() else None

        try:
            u = _locator("username_input", "username_input_fb")
            if not u:
                log_error("找不到账号输入框（页面结构可能变化）")
                self._screenshot(page, "login_no_username")
                return False
            u.click()
            time.sleep(0.3)
            u.fill(self.username)
            time.sleep(0.3)

            p = _locator("password_input", "password_input_fb")
            if not p:
                log_error("找不到密码输入框（页面结构可能变化）")
                self._screenshot(page, "login_no_password")
                return False
            p.click()
            time.sleep(0.3)
            p.fill(self.password)
            time.sleep(0.3)

            btn = _locator("login_btn", "login_btn_fb")
            if not btn:
                log_error("找不到登录按钮（页面结构可能变化）")
                self._screenshot(page, "login_no_btn")
                return False
            btn.click()
            log_info("已点击登录按钮")
            return True
        except Exception as e:
            log_error(f"自动登录填写失败: {e}")
            self._screenshot(page, "login_error")
            return False

    def _handle_verify(self, page, timeout: int = 20) -> bool:
        """等待并处理二次认证弹窗（跳过/完成登录），直到进入 ERP 页面。"""
        log_info("等待二次认证/登录完成...")
        for _ in range(timeout):
            url = (page.url or "").lower()
            if "/erp/" in url:
                log_info("登录成功，已进入 ERP 系统")
                return True

            # 二次认证之一：跳过按钮
            try:
                skip = page.locator(self.SELECTORS["verify_skip_btn"]).first
                if skip.count() > 0 and skip.is_visible():
                    skip.click()
                    log_info("已处理二次认证(跳过)")
                    time.sleep(1)
                    continue
            except Exception:
                pass

            # 二次认证之二：完成登录按钮
            try:
                done = page.locator(self.SELECTORS["verify_done_btn"]).first
                if done.count() > 0 and done.is_visible():
                    done.click()
                    log_info("已处理二次认证(完成登录)")
                    time.sleep(1)
                    continue
            except Exception:
                pass

            time.sleep(1)

        log_error(f"登录流程超时，当前URL: {page.url[:120]}")
        self._screenshot(page, "login_timeout")
        return False

    # ────────────────────────────── 获取登录态（auth-token） ──────────────────────────────

    def fetch_auth_token(self, wait_seconds: int = 15) -> str | None:
        """跳转 Listing 页面触发 showOnline 请求，捕获 auth-token，写回 .env。

        Args:
            wait_seconds: 捕获等待时长（秒）

        Returns:
            捕获到的 token 字符串；失败返回 None
        """
        pw, browser = self._connect()
        captured = {"token": None}

        def _on_request(request):
            try:
                url = request.url or ""
            except Exception:
                return
            if "showOnline" in url:
                token = ""
                try:
                    token = request.headers.get("auth-token", "")
                except Exception:
                    pass
                if not token:
                    try:
                        token = (request.all_headers().get("auth-token", "") or
                                 request.all_headers().get("Auth-Token", ""))
                    except Exception:
                        pass
                if token:
                    captured["token"] = token

        try:
            page = self._get_erp_page(browser)
            page.on("request", _on_request)

            # 若未登录，先自动登录
            if not self.is_logged_in(page):
                log_info("检测到未登录，先执行自动登录...")
                if not self._do_login(browser, page):
                    log_error("自动登录失败，无法获取 token")
                    return None

            # 跳转 Listing 页面触发 showOnline 请求
            try:
                page.goto(LINGXING_LISTING_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                log_warn("跳转 Listing 超时，仍等待捕获请求...")
            time.sleep(2)

            # 触发搜索以强制发出 showOnline 请求
            try:
                si = page.locator('input[type="text"]').first
                if si.count() > 0 and si.is_visible():
                    si.click()
                    time.sleep(0.3)
                    si.fill("B0")
                    si.press("Enter")
            except Exception:
                log_warn("搜索触发失败，等待页面自动请求...")

            # 等待捕获
            for _ in range(wait_seconds):
                if captured["token"]:
                    break
                time.sleep(1)

            token = captured["token"]
            if token:
                self._write_token_to_env(token)
                log_info(f"auth-token 已写入 .env → LX_AUTH_TOKEN")
                return token
            log_error("未捕获到 auth-token（可能浏览器未登录或页面未发出请求）")
            return None
        finally:
            try:
                pw.stop()
            except Exception:
                pass

    # ────────────────────────────── 合并入口 ──────────────────────────────

    def ensure_token(self) -> str | None:
        """确保登录并获取有效 token：未登录则自动登录，再捕获 token。"""
        token = self.fetch_auth_token()
        if not token:
            log_warn("首次获取失败，尝试自动登录后重试...")
            self.ensure_login()
            token = self.fetch_auth_token()
        return token

    # ────────────────────────────── 工具方法 ──────────────────────────────

    def _write_token_to_env(self, token: str):
        """将 token 写入 .env 的 LX_AUTH_TOKEN（兼容已有值/注释行）"""
        lines = []
        found = False
        if os.path.exists(self.env_path):
            with open(self.env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        out = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("LX_AUTH_TOKEN=") or stripped.startswith("# LX_AUTH_TOKEN"):
                out.append(f"LX_AUTH_TOKEN={token}\n")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"\nLX_AUTH_TOKEN={token}\n")

        with open(self.env_path, "w", encoding="utf-8") as f:
            f.writelines(out)

    @staticmethod
    def _screenshot(page, name: str):
        try:
            page.screenshot(path=os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs",
                f"auth_{name}_{int(time.time())}.png"))
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser(description="领星登录态独立模块")
    parser.add_argument("--cdp-port", type=int, default=None, help=f"CDP端口(默认{BROWSER_CDP_PORT})")
    parser.add_argument("--login", action="store_true", help="自动登录（含二次认证）")
    parser.add_argument("--fetch-token", action="store_true", help="捕获 auth-token 写入 .env")
    parser.add_argument("--ensure-token", action="store_true", help="确保登录并获取 token")
    args = parser.parse_args()

    auth = LingxingAuth(cdp_port=args.cdp_port)

    if args.ensure_token or (not args.login and not args.fetch_token):
        token = auth.ensure_token()
        print(f"TOKEN_OK={token[:20]}...{token[-10:]}" if token else "TOKEN_FAILED")
    elif args.login:
        ok = auth.ensure_login()
        print("LOGIN_OK" if ok else "LOGIN_FAILED")
    elif args.fetch_token:
        token = auth.fetch_auth_token()
        print(f"TOKEN_OK={token[:20]}...{token[-10:]}" if token else "TOKEN_FAILED")


if __name__ == "__main__":
    main()
