"""应用配置管理"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # 应用
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/auto_replenishment"

    # CORS（逗号分隔的来源白名单；默认 * 允许所有来源，不带凭证）
    CORS_ORIGINS: str = "*"

    # 领星API
    LX_API_BASE_URL: str = ""
    LX_APP_KEY: str = ""
    LX_APP_SECRET: str = ""
    LX_COMPANY_ID: str = ""

    # 飞书通知（应用机器人方式优先）
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    FEISHU_CHAT_ID: Optional[str] = None
    # 自定义机器人 Webhook（备用）
    FEISHU_WEBHOOK_URL: Optional[str] = None

    # 定时任务
    SYNC_INTERVAL_HOURS: int = 24
    SYNC_TIME: str = "01:00"  # 数据同步每日执行时间（HH:MM）
    CALC_INTERVAL_HOURS: int = 24
    DAILY_REPORT_TIME: str = "09:00"  # 按频率计算+日报时间（HH:MM）

    # 按等级计算频率（天），可自定义，格式: S:1,A:3,B:5,C:7,D:14
    CALC_FREQUENCIES: str = "S:1,A:3,B:5,C:7,D:14"
    # 无等级/未配置等级时的默认频率（天）
    CALC_FREQUENCY_DEFAULT: int = 14

    # 计算参数
    FORECAST_MONTHS: int = 6
    SAFE_STOCK_DAYS: int = 0
    SEA_SLOW_DAYS: int = 30
    SEA_PEAK_DAYS: int = 45
    AIR_SLOW_DAYS: int = 10
    AIR_PEAK_DAYS: int = 15
    EXPRESS_SLOW_DAYS: int = 3
    EXPRESS_PEAK_DAYS: int = 6

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
