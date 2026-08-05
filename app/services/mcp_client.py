# -*- coding: utf-8 -*-
"""
MCP 客户端 — 对接 SIF MCP 和 领星 MCP

通过 streamable-http 协议调用 MCP 工具，为自动补货决策系统提供:
  - SIF: 生命周期、核心销售月份、广告ACOS、评分评论、关键词/类目排名
  - 领星: 利润报表、广告报告、FBA库存、产品表现、补货建议

用法:
    from app.services.mcp_client import mcp_call

    # 调用领星利润报表
    result = mcp_call("lingxing", "query_order_profit_list",
                      {"msku": "xxx", "start_date": "2026-06-01", "end_date": "2026-06-30"})

    # 调用SIF销量趋势
    result = mcp_call("sif", "ops_get_asin_sales_trend",
                      {"asin": "B0DP2FLFKM"})
"""

import os
import json
import requests
import logging

logger = logging.getLogger(__name__)

# ==================== MCP 服务器配置 ====================
# 从项目 .mcp.json 读取配置
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MCP_CONFIG_PATH = os.path.join(_PROJECT_ROOT, ".mcp.json")

MCP_SERVERS = {}


def _load_mcp_config() -> dict:
    """读取 .mcp.json 配置"""
    global MCP_SERVERS
    try:
        if os.path.exists(_MCP_CONFIG_PATH):
            with open(_MCP_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            servers = cfg.get("mcp", {}).get("servers", {})
            for name, info in servers.items():
                MCP_SERVERS[name] = {
                    "url": info.get("url", ""),
                    "headers": dict(info.get("headers", {})),
                }
        else:
            logger.warning(f"未找到 MCP 配置文件: {_MCP_CONFIG_PATH}")
    except Exception as e:
        logger.error(f"读取 MCP 配置失败: {e}")
    return MCP_SERVERS


_load_mcp_config()

# MCP 服务器名称别名映射
_SERVER_ALIASES = {
    "sif": "sif-mcp",
    "lingxing": "LingXing-MCP",
    "lx": "LingXing-MCP",
}


def _resolve_server(server_name: str) -> dict:
    """解析服务器名称（支持别名）"""
    name = _SERVER_ALIASES.get(server_name.lower(), server_name)
    server = MCP_SERVERS.get(name)
    if not server:
        raise ValueError(f"未找到 MCP 服务器: {server_name} (可用: {list(MCP_SERVERS.keys())})")
    return server


class MCPClient:
    """单个 MCP 服务器的 HTTP 客户端"""

    def __init__(self, server_name: str):
        server = _resolve_server(server_name)
        self.url = server["url"]
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **server["headers"],
        }
        self.session_id = None
        self._initialized = False

    def _ensure_initialized(self):
        """确保已初始化（获取 session）"""
        if self._initialized:
            return
        resp = requests.post(
            self.url,
            headers=self.headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "auto-replenishment", "version": "1.0.0"},
                },
            },
            timeout=30,
        )
        resp.raise_for_status()
        session_id = resp.headers.get("mcp-session-id")
        if session_id:
            self.session_id = session_id
            self.headers["mcp-session-id"] = session_id

        # 发送 initialized 通知
        try:
            requests.post(
                self.url,
                headers=self.headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                timeout=10,
            )
        except Exception:
            pass

        self._initialized = True
        logger.debug(f"MCP {self.url} 初始化完成, session={session_id[:20] if session_id else 'N/A'}")

    def _parse_response(self, resp: requests.Response):
        """解析响应（支持 JSON 和 SSE 格式）"""
        content_type = resp.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            # SSE 格式，取 data: 行
            for line in resp.text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str and data_str != "[DONE]":
                        try:
                            return json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
            raise ValueError(f"SSE 响应无有效数据: {resp.text[:200]}")
        return resp.json()

    def call(self, tool_name: str, arguments: dict = None):
        """调用 MCP 工具"""
        self._ensure_initialized()
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1000000,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments or {},
            },
        }
        logger.info(f"MCP调用: {tool_name} params={json.dumps(arguments or {}, ensure_ascii=False)[:200]}")
        resp = requests.post(self.url, headers=self.headers, json=payload, timeout=120)
        resp.raise_for_status()
        data = self._parse_response(resp)

        result = data.get("result", {})
        if data.get("error"):
            raise RuntimeError(f"MCP {tool_name} 错误: {data['error']}")

        # MCP 返回的 content 是数组，通常第一个是 text
        content = result.get("content", [])
        if content and isinstance(content, list):
            for item in content:
                if item.get("type") == "text":
                    text = item["text"]
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return text
        return result.get("structuredContent") or result

    def list_tools(self):
        """列出所有可用工具"""
        self._ensure_initialized()
        payload = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1000000,
            "method": "tools/list",
            "params": {},
        }
        resp = requests.post(self.url, headers=self.headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = self._parse_response(resp)
        return data.get("result", {}).get("tools", [])


# ==================== 全局便捷函数 ====================
import time

_clients = {}


def get_client(server_name: str) -> MCPClient:
    """获取 MCP 客户端实例（缓存复用）"""
    name = _SERVER_ALIASES.get(server_name.lower(), server_name)
    if name not in _clients:
        _clients[name] = MCPClient(name)
    return _clients[name]


def mcp_call(server: str, tool: str, params: dict = None):
    """便捷调用 MCP 工具"""
    return get_client(server).call(tool, params)


def list_tools(server: str):
    """列出服务器可用工具"""
    return get_client(server).list_tools()


# ==================== 业务封装 ====================
# 领星 MCP 工具封装（解决核心数据缺口）

def lx_profit_report(msku: str, start_date: str, end_date: str, **kwargs):
    """领星订单利润报表 — 获取毛利润/毛利率（采购评分20%权重）"""
    params = {
        "search_field": "asin",
        "search_value": [msku],
        "start_date": start_date,
        "end_date": end_date,
        "summary_field": "msku",
        "turn_on_summary": "1",
        "sort_type": "desc",
        "length": 20,
        "offset": 0,
        "source_service": "mcp",
        "external_service_mark": 1,
        **kwargs,
    }
    return mcp_call("lingxing", "query_order_profit_list", params)


def lx_gross_profit(asin: str, start_date: str, end_date: str, **kwargs):
    """领星订单利润毛利报表 — 毛利率/毛利/ROI/退款"""
    params = {
        "search_field": "asin",
        "search_value": [asin],
        "start_date": start_date,
        "end_date": end_date,
        "summary_field": "msku",
        "turn_on_summary": "1",
        "length": 20,
        "offset": 0,
        **kwargs,
    }
    return mcp_call("lingxing", "query_order_profit_list_gross_profit", params)


def lx_fba_stock(msku: str = "", **kwargs):
    """领星FBA库存列表 — 可售/预留/在途/库存天数"""
    params = {
        "search_field": "seller_sku",
        "search_value": msku,
        "offset": 0,
        "length": 20,
        "sort_field": "asc",
        "sort_type": "asc",
        "is_cost_page": "0",
        **kwargs,
    }
    return mcp_call("lingxing", "get_fba_stock_list", params)


def lx_product_performance(**kwargs):
    """领星产品表现 — 广告投入/转化率/评论增长（生命周期判断依据）"""
    return mcp_call("lingxing", "query_product_performance_asin_lists", kwargs)


def lx_ad_report(**kwargs):
    """领星广告活动报告"""
    return mcp_call("lingxing", "ad_campaign_report", kwargs)


def lx_ad_product_report(**kwargs):
    """领星广告商品报告"""
    return mcp_call("lingxing", "ad_campaign_product_report", kwargs)


def lx_keyword_ranking(**kwargs):
    """领星关键词排名"""
    return mcp_call("lingxing", "query_erp_keyword_ranking_asin", kwargs)


def lx_replenishment_suggestions(**kwargs):
    """领星FBA补货建议"""
    return mcp_call("lingxing", "query_fba_valid_list", kwargs)


def lx_shop_list():
    """领星店铺列表"""
    return mcp_call("lingxing", "get_my_sids")


# SIF MCP 工具封装

def sif_sales_trend(asin: str, **kwargs):
    """SIF 销量趋势（月度历史销量，可用于核心销售月份/生命周期判断）"""
    params = {"asin": asin, **kwargs}
    return mcp_call("sif", "ops_get_asin_sales_trend", params)


def sif_sales_list(asin: str, **kwargs):
    """SIF 销量列表"""
    params = {"asin": asin, **kwargs}
    return mcp_call("sif", "ops_get_asin_sales_list", params)


def sif_asin_profile(asin: str, **kwargs):
    """SIF ASIN 市场定位画像（评分/评论/排名/BSR）"""
    params = {"asins": [asin], **kwargs}
    return mcp_call("sif", "market_get_asin_profile", params)


def sif_asin_ad_structure(asin: str, **kwargs):
    """SIF 广告结构（ACOS等）"""
    params = {"asin": asin, **kwargs}
    return mcp_call("sif", "ads_get_asin_ad_structure", params)


def sif_keyword_signals(asin: str, **kwargs):
    """SIF 关键词信号（排名变化）"""
    params = {"asin": asin, **kwargs}
    return mcp_call("sif", "market_get_asin_keyword_signals", params)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="测试 MCP 客户端")
    parser.add_argument("--list", action="store_true", help="列出工具")
    parser.add_argument("--server", default="lingxing", help="服务器 (lingxing/sif)")
    parser.add_argument("--tool", default="get_my_sids", help="工具名")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    if args.list:
        tools = list_tools(args.server)
        print(f"\n{args.server} 工具列表 ({len(tools)}):")
        for t in tools:
            print(f"  - {t['name']}")
    else:
        result = mcp_call(args.server, args.tool)
        print(f"\n调用结果: {json.dumps(result, ensure_ascii=False, indent=2)[:3000]}")
