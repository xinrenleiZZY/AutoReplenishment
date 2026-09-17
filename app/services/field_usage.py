# -*- coding: utf-8 -*-
"""字段代码引用统计 — 判断字段是否被系统代码实际使用

遍历后端 app/、前端 web/src/ 与脚本 scripts/ 的源码，统计每个标识符作为：
  1. 属性访问  .field
  2. 字典键访问  ["field"] / ['field']
  3. 字典字面量键  "field": / 'field':
出现的次数。

- used_count > 0 -> 字段被实际使用（已被系统逻辑/展示引用）
- used_count = 0 -> 字段未被任何代码引用（疑似死字段/仅存在于模型定义）

结果在内存缓存，重扫时重建；供 /api/v1/data-source 列表接口直接查表。
"""
import os
import re
from collections import Counter

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # app/
PROJECT_DIR = os.path.dirname(BASE_DIR)  # 工程根目录

BACKEND_DIR = BASE_DIR
FRONTEND_DIR = os.path.join(PROJECT_DIR, "web", "src")
SCRIPTS_DIR = os.path.join(PROJECT_DIR, "scripts")

# 后端需要排除的子目录（这些只是 ORM 列定义/迁移，并非"使用"）
BACKEND_EXCLUDE_DIRS = {"models", "migrations"}
# 后端需要排除的文件（这些只是标注/统计字段本身）
BACKEND_EXCLUDE_FILES = {
    "services/data_source_scan.py",
    "services/field_usage.py",
}

_ATTR_RE = re.compile(r"\.([A-Za-z_]\w*)")
_DICT_ACCESS_RE = re.compile(r"""\[["']([A-Za-z_]\w*)["']\]""")
_DICT_KEY_RE = re.compile(r"""["']([A-Za-z_]\w*)["']\s*:""")

_usage_cache: Counter | None = None


def _iter_files(root: str, exts: tuple):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "__pycache__"]
        for fn in filenames:
            if fn.endswith(exts):
                yield os.path.join(dirpath, fn)


def _scan_counter(counter: Counter, text: str) -> None:
    counter.update(_ATTR_RE.findall(text))
    counter.update(_DICT_ACCESS_RE.findall(text))
    counter.update(_DICT_KEY_RE.findall(text))


def build_usage_index() -> Counter:
    """扫描全部源码，返回 {标识符: 引用次数}"""
    counter: Counter = Counter()

    # 后端 app/**/*.py
    for path in _iter_files(BACKEND_DIR, (".py",)):
        rel = os.path.relpath(path, BACKEND_DIR).replace(os.sep, "/")
        if rel.split("/")[0] in BACKEND_EXCLUDE_DIRS:
            continue
        if rel in BACKEND_EXCLUDE_FILES:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                _scan_counter(counter, f.read())
        except Exception:
            continue

    # 脚本 scripts/**/*.py
    if os.path.isdir(SCRIPTS_DIR):
        for path in _iter_files(SCRIPTS_DIR, (".py",)):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _scan_counter(counter, f.read())
            except Exception:
                continue

    # 前端 web/src/**/*.{ts,tsx,js,jsx}
    if os.path.isdir(FRONTEND_DIR):
        for path in _iter_files(FRONTEND_DIR, (".ts", ".tsx", ".js", ".jsx")):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _scan_counter(counter, f.read())
            except Exception:
                continue

    return counter


def get_usage_counter() -> Counter:
    """返回带缓存的引用计数（供 API 调用）"""
    global _usage_cache
    if _usage_cache is None:
        _usage_cache = build_usage_index()
    return _usage_cache


def refresh_usage_counter() -> Counter:
    """重建缓存（扫描后调用）"""
    global _usage_cache
    _usage_cache = build_usage_index()
    return _usage_cache


def lookup(field_name: str) -> int:
    return get_usage_counter().get(field_name, 0)


if __name__ == "__main__":
    c = build_usage_index()
    print(f"扫描到的标识符总数: {len(c)}")
    for kw in ("asin", "seven_volume", "purchase_trigger", "forecast_total"):
        print(f"  {kw}: {c.get(kw, 0)}")
