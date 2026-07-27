"""工具函数"""

import json
from datetime import date, datetime
from typing import Any


class DateTimeEncoder(json.JSONEncoder):
    """支持日期时间序列化的JSON编码器"""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


def safe_json_dumps(data: Any) -> str:
    """安全地序列化JSON"""
    return json.dumps(data, cls=DateTimeEncoder, ensure_ascii=False)
