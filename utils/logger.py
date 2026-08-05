# -*- coding: utf-8 -*-
"""
日志工具模块
基于 loguru，简化日志配置
"""
import os
import sys
from datetime import datetime

from loguru import logger


# 默认日志目录
_LOG_DIR = None


def setup_logger(log_dir: str = None, level: str = "INFO"):
    """
    初始化日志配置

    参数:
        log_dir: 日志输出目录，默认 ./logs
        level: 日志级别 DEBUG/INFO/WARN/ERROR
    """
    global _LOG_DIR

    if log_dir:
        _LOG_DIR = log_dir
    else:
        _LOG_DIR = os.path.join(os.getcwd(), "logs")

    os.makedirs(_LOG_DIR, exist_ok=True)

    # 移除默认 handler
    logger.remove()

    # 控制台输出（彩色）
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:HH:mm:ss}</green> | <level>{level:7}</level> | <cyan>{message}</cyan>",
        colorize=True,
    )

    # 文件输出（每日轮换）
    log_file = os.path.join(_LOG_DIR, f"run_{datetime.now().strftime('%Y%m%d')}.log")
    logger.add(
        log_file,
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:7} | {message}",
        encoding="utf-8",
        rotation="1 day",
        retention="30 days",
    )

    return logger


def get_logger():
    """获取 logger 实例"""
    return logger


# 快捷导出
log_info = logger.info
log_debug = logger.debug
log_warn = logger.warning
log_error = logger.error
