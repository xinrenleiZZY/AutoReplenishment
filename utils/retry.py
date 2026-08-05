# -*- coding: utf-8 -*-
"""
重试装饰器模块
用于网络请求、页面操作等可能失败的操作
"""
import time
import functools


def retry(max_attempts: int = 3, delay_ms: int = 2000, backoff_factor: float = 2.0,
          exceptions: tuple = (Exception,)):
    """
    重试装饰器

    参数:
        max_attempts: 最大重试次数
        delay_ms: 初始重试延迟（毫秒）
        backoff_factor: 延迟倍数（每次重试延迟翻倍）
        exceptions: 需要重试的异常类型
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay_ms / 1000.0

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        from .logger import log_warn
                        log_warn(
                            f"{func.__name__} 第{attempt}次失败: {e}, "
                            f"{current_delay:.1f}s 后重试..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        from .logger import log_error
                        log_error(
                            f"{func.__name__} 重试{max_attempts}次后仍失败: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator


def retry_async(max_attempts: int = 3, delay_ms: int = 2000, backoff_factor: float = 2.0,
                exceptions: tuple = (Exception,)):
    """
    异步重试装饰器（为后续 Playwright 异步 API 准备）
    """
    import asyncio

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay_ms / 1000.0

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        from .logger import log_warn
                        log_warn(
                            f"{func.__name__} 第{attempt}次失败: {e}, "
                            f"{current_delay:.1f}s 后重试..."
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        from .logger import log_error
                        log_error(
                            f"{func.__name__} 重试{max_attempts}次后仍失败: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator
