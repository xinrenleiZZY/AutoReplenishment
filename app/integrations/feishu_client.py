"""
飞书 API 客户端 - 核心模块

支持：
  - 认证（tenant_access_token，带缓存）
  - 多维表格（bitable）：读取、创建、更新、批量操作
  - 电子表格（sheet）：读取、写入、追加行、设置样式
  - Wiki 云文档（docx）：读取纯文本
  - 即时通讯（IM）：发送消息、回复消息
  - Wiki 节点：获取信息、智能读取（自动识别节点类型）

使用方式：
    from feishu_client import FeishuClient

    client = FeishuClient(app_id="...", app_secret="...")
    # 或通过环境变量 FEISHU_APP_ID / FEISHU_APP_SECRET

依赖：pip install requests python-dotenv
"""

import csv
import json
import os
import time
from datetime import datetime, date
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


# ============================================================
# 异常类
# ============================================================
class FeishuAPIError(Exception):
    """飞书 API 调用异常"""

    def __init__(self, message: str, code: int = -1, http_status: int = 200):
        self.code = code
        self.http_status = http_status
        super().__init__(f"[{http_status}] code={code}: {message}")


# ============================================================
# 工具函数
# ============================================================

def _auto_timestamp(value: int) -> str:
    """将毫秒时间戳转为 YYYY-MM-DD 格式"""
    if 1_000_000_000_000 <= value <= 9_999_999_999_999:
        return datetime.fromtimestamp(value / 1000).strftime("%Y-%m-%d")
    if 10_000_000_000 <= value <= 99_999_999_999:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    return str(value)


def simplify_field_value(value: Any) -> str:
    """将飞书字段值展平为可读字符串"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return _auto_timestamp(int(value))
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict):
                if "name" in item:
                    parts.append(item["name"])
                elif "text" in item:
                    parts.append(item["text"])
                else:
                    parts.append(str(item))
            else:
                parts.append(str(item))
        return ", ".join(p for p in parts if p)
    if isinstance(value, dict):
        return value.get("text") or json.dumps(value, ensure_ascii=False)
    return str(value)


def date_to_timestamp(d: date | str) -> int:
    """将日期转为飞书日期字段所需的毫秒时间戳（北京时间）"""
    if isinstance(d, str):
        d = datetime.strptime(d, "%Y-%m-%d").date()
    return int(datetime(d.year, d.month, d.day).timestamp() * 1000)


# ============================================================
# 飞书客户端
# ============================================================
class FeishuClient:
    """飞书 API 客户端"""

    BASE_URL = "https://open.feishu.cn/open-apis"

    def __init__(self, app_id: str | None = None, app_secret: str | None = None):
        self.app_id = app_id or os.getenv("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.getenv("FEISHU_APP_SECRET", "")
        self._token: str | None = None
        self._token_expire_at: float = 0

    # ── 认证 ──────────────────────────────────────────

    def get_token(self, force: bool = False) -> str:
        """获取 tenant_access_token（带缓存）"""
        now = time.time()
        if self._token and now < self._token_expire_at - 60 and not force:
            return self._token

        resp = requests.post(
            f"{self.BASE_URL}/auth/v3/tenant_access_token/internal",
            headers={"Content-Type": "application/json; charset=utf-8"},
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(
                f"获取 token 失败: {data.get('msg', '')}",
                code=data.get("code", -1),
                http_status=resp.status_code,
            )
        self._token = data["tenant_access_token"]
        self._token_expire_at = now + data.get("expire", 7200)
        return self._token

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.get_token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """发起 API 请求并校验响应"""
        url = f"{self.BASE_URL}{path}"
        resp = requests.request(method, url, headers=self._headers, **kwargs)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(
                f"{method} {path} 失败: {data.get('msg', '')}",
                code=data.get("code", -1),
                http_status=resp.status_code,
            )
        return data

    # ── Wiki 节点 ─────────────────────────────────────

    def get_wiki_node_info(self, wiki_node_token: str) -> dict:
        """获取 Wiki 节点信息"""
        data = self._request("GET", "/wiki/v2/spaces/get_node", params={"token": wiki_node_token})
        return data.get("data", {}).get("node", {})

    # ── 多维表格：元信息 ───────────────────────────────

    def get_bitable_info(self, app_token: str) -> dict:
        """获取多维表格应用信息"""
        data = self._request("GET", f"/bitable/v1/apps/{app_token}")
        return data["data"]["app"]

    def resolve_wiki_bitable(self, wiki_node_token: str) -> str:
        """通过 Wiki 节点 token 获取多维表格的 app_token"""
        node = self.get_wiki_node_info(wiki_node_token)
        if node.get("obj_type") != "bitable":
            raise FeishuAPIError(f"节点类型不是多维表格: {node.get('obj_type')}", code=-1)
        return node["obj_token"]

    def list_fields(self, app_token: str, table_id: str) -> list[dict]:
        """列出多维表格所有字段"""
        data = self._request(
            "GET",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            params={"page_size": 200},
        )
        return data.get("data", {}).get("items", [])

    def create_field(self, app_token: str, table_id: str, field_name: str,
                     field_type: int = 3, property_: dict | None = None) -> dict:
        """创建字段
        field_type: 3=单选, 4=多选, 1=文本, ...
        property: {"options": [{"name": "...", "color": 10}]} 用于单选/多选
        """
        payload = {"field_name": field_name, "type": field_type}
        if property_:
            payload["property"] = property_
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
            json=payload,
        )
        return data.get("data", {}).get("field", {})

    def ensure_select_field(self, app_token: str, table_id: str, field_name: str,
                            options: list[dict]) -> bool:
        """确保单选字段存在（不存在则创建），返回是否已就绪"""
        fields = self.list_fields(app_token, table_id)
        for f in fields:
            if f.get("field_name") == field_name:
                return True
        try:
            self.create_field(app_token, table_id, field_name, field_type=3,
                              property_={"options": options})
            return True
        except Exception as e:
            print(f"  [WARN] 创建字段失败: {e}")
            return False

    # ── 多维表格：记录操作 ─────────────────────────────

    def list_records(
        self,
        app_token: str,
        table_id: str,
        view_id: str | None = None,
        page_size: int = 500,
        progress_callback=None,
    ) -> list[dict]:
        """列出多维表格所有记录（自动分页）"""
        all_records: list[dict] = []
        page_token: str | None = None
        page_count = 0

        while True:
            params = {
                "page_size": min(page_size, 500),
                "text_field_as_array": False,
                "automatic_fields": True,
            }
            if view_id:
                params["view_id"] = view_id
            if page_token:
                params["page_token"] = page_token

            data = self._request(
                "GET",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                params=params,
            )
            # 1254002 表示数据尚未就绪，重试一次
            if data.get("code") == 1254002:
                print(f"  [RETRY] 数据未就绪，等待后重试...")
                time.sleep(2)
                data = self._request(
                    "GET",
                    f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
                    params=params,
                )
            items = data.get("data", {}).get("items", [])
            all_records.extend(items)
            page_count += 1

            if progress_callback:
                progress_callback(page_count, len(items), len(all_records))

            if not data.get("data", {}).get("has_more", False):
                break
            page_token = data["data"].get("page_token")
            time.sleep(0.1)

        return all_records

    def create_record(self, app_token: str, table_id: str, fields: dict) -> dict:
        """创建单条记录"""
        data = self._request(
            "POST",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records",
            json={"fields": fields},
        )
        return data["data"]["record"]

    def batch_create_records(self, app_token: str, table_id: str, records: list[dict]) -> list[dict]:
        """批量创建记录（每次最多 500 条）"""
        result: list[dict] = []
        for i in range(0, len(records), 500):
            batch = records[i : i + 500]
            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_create",
                json={"records": [{"fields": r} for r in batch]},
            )
            result.extend(data["data"].get("records", []))
            time.sleep(0.1)
        return result

    def update_record(self, app_token: str, table_id: str, record_id: str, fields: dict) -> dict:
        """更新单条记录"""
        data = self._request(
            "PUT",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
            json={"fields": fields},
        )
        return data["data"]["record"]

    def batch_update_records(self, app_token: str, table_id: str, records: list[tuple[str, dict]]) -> list[dict]:
        """批量更新记录"""
        result: list[dict] = []
        for i in range(0, len(records), 500):
            batch = records[i : i + 500]
            payload = {"records": [{"record_id": rid, "fields": f} for rid, f in batch]}
            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_update",
                json=payload,
            )
            result.extend(data["data"].get("records", []))
            time.sleep(0.1)
        return result

    def delete_record(self, app_token: str, table_id: str, record_id: str) -> dict:
        """删除单条记录"""
        data = self._request(
            "DELETE",
            f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}",
        )
        return data.get("data", {})

    def batch_delete_records(self, app_token: str, table_id: str, record_ids: list[str]) -> dict:
        """批量删除记录（每次最多 500 条）"""
        result: list[dict] = []
        for i in range(0, len(record_ids), 500):
            batch = record_ids[i : i + 500]
            data = self._request(
                "POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/batch_delete",
                json={"records": batch},
            )
            result.extend(data.get("data", {}).get("records", []))
            time.sleep(0.1)
        return result

    # ── 电子表格 ──────────────────────────────────────

    def resolve_wiki_sheet(self, wiki_node_token: str) -> tuple[str, str]:
        """通过 Wiki 节点 token 获取电子表格 token 和类型"""
        node = self.get_wiki_node_info(wiki_node_token)
        obj_type = node.get("obj_type", "")
        obj_token = node.get("obj_token", "")
        if obj_type not in ("sheet", "bitable"):
            raise FeishuAPIError(f"节点类型不是表格: {obj_type}", code=-1)
        return obj_token, obj_type

    def get_sheet_meta(self, spreadsheet_token: str) -> dict:
        """获取电子表格元信息（工作表列表、行列数等）"""
        return self._request("GET", f"/sheets/v2/spreadsheets/{spreadsheet_token}/metainfo")

    def get_sheet_values(self, spreadsheet_token: str, sheet_id: str, range_str: str = "A1:Z10") -> list[list]:
        """读取电子表格指定范围数据"""
        url = f"{self.BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values/{sheet_id}!{range_str}"
        resp = requests.get(url, headers=self._headers)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"读取电子表格失败: {data.get('msg', '')}", code=data.get("code", -1), http_status=resp.status_code)
        return data.get("data", {}).get("valueRange", {}).get("values", [])

    def get_sheet_headers(self, spreadsheet_token: str, sheet_id: str) -> list:
        """读取电子表格第一行（表头）"""
        values = self.get_sheet_values(spreadsheet_token, sheet_id, "A1:Z1")
        return values[0] if values else []

    def append_sheet_rows(self, spreadsheet_token: str, sheet_id: str, rows: list[list]) -> dict:
        """向电子表格追加行数据（自动定位空行）"""
        values = self.get_sheet_values(spreadsheet_token, sheet_id, "A:A")
        target_row = len(values) + 1
        col_count = len(rows[0]) if rows else 1
        end_col = chr(min(col_count - 1, 25) + 65)
        range_str = f"{sheet_id}!A{target_row}:{end_col}{target_row}"
        url = f"{self.BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        payload = {"valueRange": {"range": range_str, "values": rows}}
        resp = requests.put(url, headers=self._headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"追加行失败: {data.get('msg', '')}", code=data.get("code", -1), http_status=resp.status_code)
        return data.get("data", {})

    def update_sheet_values(self, spreadsheet_token: str, sheet_id: str, range_str: str, values: list[list]) -> dict:
        """更新电子表格指定范围的值"""
        url = f"{self.BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/values"
        full_range = f"{sheet_id}!{range_str}"
        payload = {"valueRange": {"range": full_range, "values": values}}
        resp = requests.put(url, headers=self._headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"更新失败: {data.get('msg', '')}", code=data.get("code", -1), http_status=resp.status_code)
        return data.get("data", {})

    def set_sheet_style(self, spreadsheet_token: str, sheet_id: str, range_str: str,
                        font_name: str = "SimSun", font_size: int = 11, h_align: int = 0) -> dict:
        """设置单元格样式"""
        url = f"{self.BASE_URL}/sheets/v2/spreadsheets/{spreadsheet_token}/style"
        payload = {
            "appendStyle": {
                "range": f"{sheet_id}!{range_str}",
                "style": {
                    "font": {"bold": False, "italic": False, "fontSize": f"{font_size}pt/1.5", "font": font_name},
                    "hAlign": h_align,
                },
            },
        }
        resp = requests.put(url, headers=self._headers, json=payload)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuAPIError(f"设置样式失败: {data.get('msg', '')}", code=data.get("code", -1), http_status=resp.status_code)
        return data.get("data", {})

    def import_sheet_data(self, spreadsheet_token: str, payload: dict) -> dict:
        """按结构化方式导入数据到电子表格"""
        sheet_name = payload.get("name", "")
        data_block = payload.get("data", {})
        rows = data_block.get("rows", [])
        headers_input = data_block.get("headers")
        if not sheet_name:
            raise ValueError("payload 中缺少 name（工作表名称）")
        if not rows:
            raise ValueError("payload.data.rows 为空，无数据可导入")
        meta = self.get_sheet_meta(spreadsheet_token)
        target_sheet = next((sh for sh in meta.get("data", {}).get("sheets", []) if sh["title"] == sheet_name), None)
        if not target_sheet:
            raise FeishuAPIError(f"未找到名称为 '{sheet_name}' 的工作表")
        sheet_id = target_sheet["sheetId"]
        col_count = target_sheet.get("columnCount", 20)
        if headers_input:
            existing_headers = self.get_sheet_headers(spreadsheet_token, sheet_id)
            existing_clean = [h for h in existing_headers if h is not None]
            input_clean = [h for h in headers_input if h is not None]
            if existing_clean[:len(input_clean)] != input_clean:
                raise FeishuAPIError(f"表头不匹配！期望: {input_clean}，实际: {existing_clean[:len(input_clean)]}")
        full_rows = [list(row) + [None] * (col_count - len(row)) for row in rows]
        result = self.append_sheet_rows(spreadsheet_token, sheet_id, full_rows)
        return {"row_count": len(rows), "updated_range": result.get("updatedRange", ""), "sheet_id": sheet_id}

    # ── 云文档 ────────────────────────────────────────

    def get_document_raw_content(self, document_id: str) -> str:
        """获取云文档纯文本内容（docx）"""
        data = self._request("GET", f"/docx/v1/documents/{document_id}/raw_content")
        return data.get("data", {}).get("content", "")

    def read_wiki_document(self, wiki_node_token: str) -> str:
        """读取 Wiki 节点下的文档纯文本"""
        node = self.get_wiki_node_info(wiki_node_token)
        if node.get("obj_type") != "docx":
            raise FeishuAPIError(f"节点类型不是云文档: {node.get('obj_type')}", code=-1)
        return self.get_document_raw_content(node["obj_token"])

    # ── 智能读取 ──────────────────────────────────────

    def read_wiki_node(self, wiki_node_token: str, **context) -> dict:
        """智能读取 Wiki 节点内容（自动识别类型）"""
        node = self.get_wiki_node_info(wiki_node_token)
        obj_type = node.get("obj_type", "")
        obj_token = node.get("obj_token", "")
        name = node.get("title", "")
        result = {"type": obj_type, "name": name, "obj_token": obj_token}
        if obj_type == "bitable":
            table_id = context.get("table_id") or os.getenv("TABLE_ID", "")
            view_id = context.get("view_id") or os.getenv("VIEW_ID", "")
            if not table_id:
                raise FeishuAPIError("读取多维表格需要 table_id，请传入或设置 TABLE_ID 环境变量")
            records = self.list_records(obj_token, table_id, view_id)
            result["data"] = records
            result["count"] = len(records)
        elif obj_type == "sheet":
            metas = self.get_sheet_meta(obj_token)
            sheets_data = metas.get("data", {}).get("sheets", [])
            all_data = {}
            for sh in sheets_data:
                sid = sh["sheetId"]
                title = sh["title"]
                row_c = sh.get("rowCount", 100)
                col_c = sh.get("columnCount", 20)
                col_letter = chr(min(col_c - 1, 25) + 65) if col_c <= 26 else "Z"
                values = self.get_sheet_values(obj_token, sid, f"A1:{col_letter}{min(row_c, 200)}")
                all_data[title] = {"sheet_id": sid, "headers": values[0] if values else [], "rows": values[1:] if len(values) > 1 else []}
            result["data"] = all_data
            result["sheets"] = [{"id": s["sheetId"], "title": s["title"]} for s in sheets_data]
        elif obj_type == "docx":
            result["data"] = self.get_document_raw_content(obj_token)
        else:
            raise FeishuAPIError(f"暂不支持的节点类型: {obj_type}", code=-1)
        return result

    # ── 数据导出 ──────────────────────────────────────

    @staticmethod
    def flatten_bitable_record(record: dict) -> dict:
        """将多维表格的一条记录展平为扁平字典"""
        flat = {"record_id": record.get("record_id", "")}
        for field_name, field_value in (record.get("fields") or {}).items():
            flat[field_name] = simplify_field_value(field_value)
        return flat

    @staticmethod
    def records_to_csv(records: list[dict], output_path: str, field_order: list[str] | None = None):
        """将展平后的记录导出为 CSV 文件"""
        if not records:
            print("[WARN] 没有数据可导出")
            return
        if field_order:
            sorted_keys = list(field_order)
            extra = sorted(set(records[0].keys()) - set(sorted_keys))
            sorted_keys.extend(extra)
        else:
            sorted_keys = list(records[0].keys())
        with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=sorted_keys)
            writer.writeheader()
            writer.writerows(records)
        print(f"[OK] 已导出 {len(records)} 条记录到: {output_path}")

    # ── 即时通讯（IM） ────────────────────────────────

    def list_chat_messages(self, chat_id: str, page_size: int = 50,
                           start_time: str | None = None, end_time: str | None = None,
                           page_token: str | None = None) -> dict:
        """获取会话历史消息"""
        params = {"container_id_type": "chat", "container_id": chat_id, "page_size": str(min(page_size, 50))}
        if start_time:
            params["start_time"] = start_time
        if end_time:
            params["end_time"] = end_time
        if page_token:
            params["page_token"] = page_token
        return self._request("GET", "/im/v1/messages", params=params)

    def send_text_message(self, chat_id: str, text: str) -> dict:
        """向群聊发送文本消息"""
        content = json.dumps({"text": text}, ensure_ascii=False)
        data = self._request("POST", "/im/v1/messages", params={"receive_id_type": "chat_id"},
                             json={"receive_id": chat_id, "msg_type": "text", "content": content})
        return data.get("data", {})

    def reply_message(self, message_id: str, text: str) -> dict:
        """回复消息"""
        content = json.dumps({"text": text}, ensure_ascii=False)
        data = self._request("POST", f"/im/v1/messages/{message_id}/reply",
                             json={"msg_type": "text", "content": content})
        return data.get("data", {})
