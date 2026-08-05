# -*- coding: utf-8 -*-
"""
路径处理工具模块
"""
import os


def ensure_dir(path: str) -> str:
    """
    确保目录存在，不存在则创建
    返回: 原路径
    """
    os.makedirs(path, exist_ok=True)
    return path


def normalize_nas_path(path: str) -> str:
    """
    标准化 NAS 路径
    将网络路径格式转为 Python 可用格式
    """
    # 确保路径以双反斜杠开头
    if path.startswith("\\\\"):
        return path
    if path.startswith("\\"):
        return "\\" + path
    return path


def is_nas_accessible(path: str) -> bool:
    """
    检查 NAS 路径是否可访问
    """
    try:
        return os.path.exists(path)
    except (PermissionError, OSError):
        return False
