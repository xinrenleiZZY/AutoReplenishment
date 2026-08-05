# -*- coding: utf-8 -*-
"""
NAS 网络驱动器连接模块
职责：建立 NAS 的网络连接，支持账号密码认证
"""
import subprocess
import os
import time

from utils.logger import log_info, log_error, log_warn


def ensure_nas_connected(nas_path: str, username: str = None, password: str = None) -> bool:
    """
    确保 NAS 路径可访问。
    如果无法直接访问，尝试用 net use 建立连接。

    参数:
        nas_path: NAS 路径，如 \\\\192.168.40.3\\00亚马逊运营部图片视频
        username: NAS 用户名（可为空，部分NAS不需要）
        password: NAS 密码（可为空）

    返回: True=连接成功, False=连接失败
    """
    # 先检查是否已经可以访问
    if os.path.exists(nas_path):
        log_info(f"NAS 已可访问: {nas_path}")
        return True

    # 没有账号密码，直接返回失败
    if not username:
        log_warn(f"NAS 无法访问且未配置账号: {nas_path}")
        return False

    # 用 net use 建立连接
    log_info(f"正在连接 NAS: {nas_path}")

    # 提取 UNC 路径的根部分 \\server\share
    parts = nas_path.strip("\\").split("\\")
    if len(parts) >= 2:
        unc_root = "\\\\" + parts[0] + "\\" + parts[1]
    else:
        unc_root = nas_path

    try:
        # 先断开已有连接（避免凭证冲突）
        subprocess.run(
            ["net", "use", unc_root, "/delete", "/y"],
            capture_output=True, text=True, timeout=10
        )
    except Exception:
        pass

    try:
        # 建立新连接
        cmd = ["net", "use", unc_root, password, f"/USER:{username}"]
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=30,
            encoding="gbk", errors="replace",
        )

        if result.returncode == 0:
            log_info(f"NAS 连接成功: {unc_root}")
            # 等待一下让系统刷新
            time.sleep(1)
            return True
        else:
            error_msg = result.stderr or result.stdout or "未知错误"
            log_error(f"NAS 连接失败: {error_msg}")
            return False

    except subprocess.TimeoutExpired:
        log_error("NAS 连接超时")
        return False
    except FileNotFoundError:
        log_error("系统中未找到 net 命令，无法建立 NAS 连接")
        return False
    except Exception as e:
        log_error(f"NAS 连接异常: {e}")
        return False


def disconnect_nas(nas_path: str):
    """断开 NAS 连接"""
    parts = nas_path.strip("\\").split("\\")
    if len(parts) >= 2:
        unc_root = "\\\\" + parts[0] + "\\" + parts[1]
        try:
            subprocess.run(
                ["net", "use", unc_root, "/delete", "/y"],
                capture_output=True, text=True, timeout=10
            )
            log_info(f"NAS 已断开: {unc_root}")
        except Exception as e:
            log_error(f"断开 NAS 失败: {e}")
