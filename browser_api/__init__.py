# -*- coding: utf-8 -*-
"""
browser-api — 浏览器 + 登录独立子包

用法:
    from browser_api import BrowserEngine, LingxingLogin

    engine = BrowserEngine()
    engine.connect_cdp("http://127.0.0.1:18800")

    login = LingxingLogin(engine.page)
    login.login()
"""
from .browser_engine import BrowserEngine
from .login import LingxingLogin
