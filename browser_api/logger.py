# -*- coding: utf-8 -*-
"""browser-api 独立日志模块"""
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime

_logger = None
_log_dir = None

def setup_logger(log_dir: str = None, level: str = "INFO"):
    global _logger, _log_dir
    if log_dir:
        _log_dir = log_dir
    else:
        _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _log_dir = os.path.join(_project_root, "logs")
    os.makedirs(_log_dir, exist_ok=True)

    _logger = logging.getLogger("browser-api")
    if _logger.handlers:
        return
    _logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    fmtr = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    # 控制台
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmtr)
    _logger.addHandler(ch)

    # 文件
    log_path = os.path.join(_log_dir, f"browser_api_{datetime.now().strftime('%Y%m%d')}.log")
    fh = RotatingFileHandler(log_path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmtr)
    _logger.addHandler(fh)


def _ensure():
    if _logger is None:
        setup_logger()


def log_info(msg):    _ensure(); _logger.info(msg)
def log_error(msg):   _ensure(); _logger.error(msg)
def log_warn(msg):    _ensure(); _logger.warning(msg)
