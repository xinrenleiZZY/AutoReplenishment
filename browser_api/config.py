# -*- coding: utf-8 -*-
"""
browser-api 内部配置
与主项目 config.py 分离，独立维护
"""
import os

# 加载 .env（路径相对于项目根目录）
from dotenv import load_dotenv
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_project_root, ".env"))

# ====================== 领星系统 ======================
LINGXING_BASE_URL = "https://huizhixin.lingxing.com"
LINGXING_LISTING_URL = "https://huizhixin.lingxing.com/erp/listing"
LINGXING_PUBLISH_QUEUE_URL = "https://huizhixin.lingxing.com/erp/PublishManageV2"

# ====================== 浏览器配置 ======================
BROWSER_HEADLESS = False
BROWSER_TIMEOUT_MS = 30000
BROWSER_SLOW_MO_MS = 200
SCREENSHOT_ON_ERROR = True
BROWSER_CDP_PORT = 18800
BROWSER_USER_DATA_DIR = os.path.join(_project_root, "playwright_profile")

# ====================== 日志 ======================
LOG_DIR = os.path.join(_project_root, "logs")
LOG_LEVEL = "INFO"
