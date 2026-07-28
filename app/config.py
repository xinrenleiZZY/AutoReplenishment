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

    # 领星API
    LX_API_BASE_URL: str = ""
    LX_APP_KEY: str = ""
    LX_APP_SECRET: str = ""
    LX_COMPANY_ID: str = ""

    # 飞书通知
    FEISHU_WEBHOOK_URL: Optional[str] = None

    # 定时任务
    SYNC_INTERVAL_HOURS: int = 24
    CALC_INTERVAL_HOURS: int = 24
    DAILY_REPORT_TIME: str = "09:00"

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
