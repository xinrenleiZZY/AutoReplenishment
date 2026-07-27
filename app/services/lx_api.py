"""领星ERP API 客户端"""

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LxApiError(Exception):
    """领星API调用异常"""
    pass


class LxApiClient:
    """领星ERP API 客户端"""

    def __init__(self):
        self.base_url = settings.LX_API_BASE_URL
        self.app_key = settings.LX_APP_KEY
        self.app_secret = settings.LX_APP_SECRET
        self.company_id = settings.LX_COMPANY_ID

    def _sign(self, params: Dict[str, Any]) -> str:
        """生成API签名"""
        sorted_keys = sorted(params.keys())
        sign_str = self.app_secret
        for key in sorted_keys:
            sign_str += f"{key}{params[key]}"
        sign_str += self.app_secret
        return hashlib.md5(sign_str.encode()).hexdigest().upper()

    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """发送API请求"""
        url = urljoin(self.base_url, endpoint)
        params = params or {}

        # 公共参数
        params["app_key"] = self.app_key
        params["timestamp"] = int(time.time())
        params["sign"] = self._sign(params)

        headers = {
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, json=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

                if data.get("code") != 0:
                    raise LxApiError(f"API错误: {data.get('message', '未知错误')}")

                return data.get("data", {})
            except httpx.HTTPError as e:
                raise LxApiError(f"HTTP请求失败: {e}")

    async def get_products(self, page: int = 1, page_size: int = 100) -> List[Dict]:
        """获取产品列表"""
        data = await self._request("/product/list", {
            "company_id": self.company_id,
            "page": page,
            "page_size": page_size,
        })
        return data.get("list", [])

    async def get_sales_data(self, asin: str, start_date: str, end_date: str) -> List[Dict]:
        """获取销量数据"""
        data = await self._request("/sales/daily", {
            "company_id": self.company_id,
            "asin": asin,
            "start_date": start_date,
            "end_date": end_date,
        })
        return data.get("list", [])

    async def get_inventory(self, asin: str) -> Optional[Dict]:
        """获取库存数据"""
        data = await self._request("/inventory/detail", {
            "company_id": self.company_id,
            "asin": asin,
        })
        return data

    async def get_fba_inventory(self, asin: str) -> Optional[Dict]:
        """获取FBA库存明细"""
        data = await self._request("/fba/inventory", {
            "company_id": self.company_id,
            "asin": asin,
        })
        return data

    async def sync_all_sales(self, session) -> int:
        """同步所有产品的销量数据（骨架）"""
        # TODO: Phase 1 完整实现
        # 1. 获取所有产品列表
        # 2. 遍历调用 get_sales_data
        # 3. 写入 sales_data 表
        logger.info("销量同步 - 待实现完整逻辑")
        return 0

    async def sync_all_inventory(self, session) -> int:
        """同步所有产品的库存数据（骨架）"""
        # TODO: Phase 1 完整实现
        # 1. 获取所有产品列表
        # 2. 遍历调用 get_inventory / get_fba_inventory
        # 3. 写入 inventory_snapshots 表
        logger.info("库存同步 - 待实现完整逻辑")
        return 0
