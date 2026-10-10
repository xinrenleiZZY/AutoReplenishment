# -*- coding: utf-8 -*-
"""
浏览器独立启动器（独立版）
用法: python -m browser-api.browser_starter [--port 18800] [--bind 0.0.0.0]
"""
import sys
import os
import argparse
import subprocess
import socket
import threading

# 确定项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from .config import BROWSER_CDP_PORT, BROWSER_USER_DATA_DIR
from .logger import setup_logger, log_info, log_error, log_warn


def check_port_in_use(port: int) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.5)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        return result == 0
    except Exception:
        return False


def cleanup_zombie_processes(port: int):
    """清理占用指定端口的残留 Chrome 进程"""
    if check_port_in_use(port):
        log_info(f"端口 {port} 被占用，尝试清理...")
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    parts = line.strip().split()
                    pid = parts[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                   capture_output=True, timeout=10)
                    log_info(f"  已终止 PID={pid}")
                    break
        except Exception as e:
            log_warn(f"  清理失败: {e}")


def launch_browser(port: int = None, cleanup: bool = True, bind_address: str = None):
    """启动独立浏览器（使用 Playwright 异步 API 避免事件循环冲突）

    Args:
        port: CDP 调试端口
        cleanup: 是否清理占用端口残留进程
        bind_address: CDP 监听地址（默认 127.0.0.1；如需容器内访问可传 0.0.0.0）
    """
    import asyncio
    from playwright.async_api import async_playwright

    if port is None:
        port = BROWSER_CDP_PORT
    if bind_address is None:
        bind_address = os.getenv("BROWSER_CDP_BIND", "127.0.0.1")

    if cleanup:
        cleanup_zombie_processes(port)

    async def _launch():
        pw = await async_playwright().start()
        try:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                channel="chrome",
                headless=False,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={port}",
                    f"--remote-debugging-address={bind_address}",
                ],
            )
            log_info("使用本地 Chrome 浏览器")
        except Exception:
            log_info("本地 Chrome 不可用，回退到 Playwright 内置 Chromium")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=BROWSER_USER_DATA_DIR,
                headless=False,
                no_viewport=True,
                locale="zh-CN",
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    f"--remote-debugging-port={port}",
                    f"--remote-debugging-address={bind_address}",
                ],
            )

        page = await context.new_page()
        log_info(f"浏览器已启动，CDP 端口: {port}")
        log_info("请在浏览器中操作，关闭浏览器窗口即退出。")
        log_info("Ctrl+C 可安全退出（浏览器保持打开）。")

        # 内置 TCP 转发器：监听 0.0.0.0:(port+99)，转发到 127.0.0.1:port
        # 用途：Docker 容器内无法直连宿主 loopback 的 CDP（Chrome 强制仅 loopback 来源），
        #       转发器从外部接入后以 loopback 身份连接 Chrome，使容器内(host.docker.internal)也能用 CDP。
        forwarder = None
        if bind_address not in ("127.0.0.1", "localhost"):
            fw_port = port + 99
            forwarder = _start_tcp_forwarder(fw_port, "127.0.0.1", port)
            if forwarder:
                log_info(f"TCP 转发器已启动: 0.0.0.0:{fw_port} -> 127.0.0.1:{port}（供 Docker 容器访问）")

        try:
            while True:
                await asyncio.sleep(1)
                try:
                    for p in context.pages:
                        pass
                except Exception:
                    log_info("浏览器窗口已关闭，退出。")
                    break
        except KeyboardInterrupt:
            log_info("收到退出信号")
        finally:
            if forwarder:
                try:
                    forwarder.close()
                except Exception:
                    pass

        await pw.stop()

    asyncio.run(_launch())


def _start_tcp_forwarder(listen_port: int, target_host: str, target_port: int):
    """启动 TCP 端口转发器（线程），返回 server socket（可 close 关闭）。

    任一连接被转发到目标后双向透传。用于把外部访问的 CDP 请求以 loopback 身份转发给 Chrome。
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", listen_port))
        srv.listen(50)
    except Exception as e:
        log_warn(f"转发器监听 {listen_port} 失败: {e}")
        return None

    def _pipe(src: socket.socket, dst: socket.socket):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except Exception:
            pass
        finally:
            try:
                src.close()
            except Exception:
                pass
            try:
                dst.close()
            except Exception:
                pass

    def _accept():
        while True:
            try:
                conn, _ = srv.accept()
            except Exception:
                return
            try:
                target = socket.create_connection((target_host, target_port), timeout=10)
            except Exception:
                conn.close()
                continue
            threading.Thread(target=_pipe, args=(conn, target), daemon=True).start()
            threading.Thread(target=_pipe, args=(target, conn), daemon=True).start()

    threading.Thread(target=_accept, daemon=True).start()
    return srv


def main():
    parser = argparse.ArgumentParser(description="浏览器独立启动器")
    parser.add_argument("--port", type=int, default=BROWSER_CDP_PORT, help=f"CDP 端口（默认 {BROWSER_CDP_PORT}）")
    parser.add_argument("--bind", default=None, help="CDP 监听地址（默认 127.0.0.1；Docker 容器内访问可传 0.0.0.0）")
    parser.add_argument("--no-cleanup", action="store_true", help="不清理残留进程")
    args = parser.parse_args()

    setup_logger()
    log_info(f"浏览器独立启动器（CDP 端口: {args.port}，监听: {args.bind or os.getenv('BROWSER_CDP_BIND', '127.0.0.1')}）")
    launch_browser(args.port, cleanup=not args.no_cleanup, bind_address=args.bind)


if __name__ == "__main__":
    main()
