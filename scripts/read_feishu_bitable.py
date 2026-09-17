# -*- coding: utf-8 -*-
"""读取飞书多维表格（Bitable）：获取在途商品的上架时间

数据源（在途货件多维表格）：
    品名 / ASIN / 货件单号 / 发货量 / 物流渠道 / 跟踪编号 / 开船时间 /
    预计到港时间 / 是否查验 / 数据期间

上架时间口径：预计到港时间 + 上架缓冲天数（--arrival-buffer，默认 3 天；
若到港即上架可传 0）。

用法：
    python -m scripts.read_feishu_bitable "<wiki链接>"
    python -m scripts.read_feishu_bitable --wiki-token Ft4mwtFoSiImBJkhtp5cgsTvnzg \\
        --table-id tblMWIP7Lq3VsRHd --view-id vewwPyExXx --arrival-buffer 3
    python -m scripts.read_feishu_bitable "<wiki链接>" --csv p_id/intransit_arrival.csv

依赖：.env 中 FEISHU_APP_ID / FEISHU_APP_SECRET（应用需有该多维表格的查看权限）。
复用 app/integrations/feishu_client.py（wiki 解析 + bitable 分页读取）。
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

load_dotenv()

from app.config import settings  # noqa: E402
from app.integrations.feishu_client import FeishuClient  # noqa: E402

# 表格字段名（宽松匹配：精确匹配优先，其次包含匹配）
FIELD_ALIASES = {
    "品名": ("品名", "产品名", "产品名称"),
    "asin": ("ASIN", "asin"),
    "货件单号": ("货件单号", "货件号", "shipment"),
    "发货量": ("发货量", "数量", "件数"),
    "物流渠道": ("物流渠道", "渠道", "物流方式"),
    "跟踪编号": ("跟踪编号", "跟踪号", "tracking"),
    "开船时间": ("开船时间", "开船日期"),
    "预计到港时间": ("预计到港时间", "预计到港", "到港时间"),
    "是否查验": ("是否查验", "查验"),
    "数据期间": ("数据期间", "期间"),
}


def _match_field(flat: dict, key: str) -> str:
    """按别名表在展平字段中找字段名"""
    for alias in FIELD_ALIASES[key]:
        if alias in flat:
            return alias
    return ""


def _to_int(v) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _parse_date(v, year: int | None = None) -> date | None:
    """日期字符串 → date

    支持：YYYY-MM-DD / YYYY/M/D / M/D（M/D 无年份时用 year 推断，默认当年）。
    """
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = re.match(r"^(\d{1,2})[/-](\d{1,2})", s)
        if m:
            y = year or date.today().year
            return date(y, int(m.group(1)), int(m.group(2)))
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def parse_record(flat: dict) -> dict:
    """展平记录 → 结构化 dict"""
    period = flat.get(_match_field(flat, "数据期间"), "")
    period_year = None
    m = re.search(r"(\d{4})[-/]\d{1,2}[-/]\d{1,2}", str(period))
    if m:
        period_year = int(m.group(1))
    return {
        "record_id": flat.get("record_id", ""),
        "品名": flat.get(_match_field(flat, "品名"), ""),
        "ASIN": str(flat.get(_match_field(flat, "asin"), "")).strip().upper(),
        "货件单号": flat.get(_match_field(flat, "货件单号"), ""),
        "发货量": _to_int(flat.get(_match_field(flat, "发货量"))),
        "物流渠道": flat.get(_match_field(flat, "物流渠道"), ""),
        "跟踪编号": flat.get(_match_field(flat, "跟踪编号"), ""),
        "开船时间": _parse_date(flat.get(_match_field(flat, "开船时间")), period_year),
        "预计到港时间": _parse_date(flat.get(_match_field(flat, "预计到港时间")), period_year),
        "是否查验": flat.get(_match_field(flat, "是否查验"), ""),
        "数据期间": flat.get(_match_field(flat, "数据期间"), ""),
    }


def original_strategy_arrival(today: date, buffer_days: int) -> date:
    """原有策略兜底：在途预计上架日 = 今天 + 海运时效(旺季45/淡季30) + 上架缓冲

    与系统 calculation_tasks._analyze_inventory 的 inbound_arrival_date 口径一致。
    """
    sea_days = settings.SEA_PEAK_DAYS if 8 <= today.month <= 12 else settings.SEA_SLOW_DAYS
    return today + timedelta(days=sea_days + buffer_days)


def main():
    parser = argparse.ArgumentParser(description="读取飞书多维表格：在途商品上架时间")
    parser.add_argument("url", nargs="?", help="wiki 链接（含 wiki token / table / view）")
    parser.add_argument("--wiki-token", help="wiki 节点 token（不传 URL 时使用）")
    parser.add_argument("--table-id", help="多维表格 table_id（默认取 URL 中 table= 参数）")
    parser.add_argument("--view-id", help="视图 view_id（默认取 URL 中 view= 参数）")
    parser.add_argument("--arrival-buffer", type=int, default=3,
                        help="预计到港后到上架的天数（默认 3；到港即上架传 0）")
    parser.add_argument("--year", type=int, default=None,
                        help="M/D 短日期年份（默认从“数据期间”推断，无则当年）")
    parser.add_argument("--csv", default=os.path.join(BASE_DIR, "p_id", "feishu_intransit_arrival.csv"),
                        help="CSV 输出路径")
    parser.add_argument("--json-out", help="可选：JSON 输出路径")
    parser.add_argument("--all", action="store_true",
                        help="输出全部记录（默认只输出上架时间≥今天=在途）")
    args = parser.parse_args()

    url = args.url or ""
    wiki_token = args.wiki_token or (re.search(r"wiki/([A-Za-z0-9]+)", url).group(1) if re.search(r"wiki/([A-Za-z0-9]+)", url) else "")
    table_id = args.table_id or (re.search(r"table=([A-Za-z0-9]+)", url).group(1) if re.search(r"table=([A-Za-z0-9]+)", url) else "")
    view_id = args.view_id or (re.search(r"view=([A-Za-z0-9]+)", url).group(1) if re.search(r"view=([A-Za-z0-9]+)", url) else "")
    if not wiki_token or not table_id:
        raise SystemExit("缺少 wiki token 或 table id：请传 wiki 链接或 --wiki-token/--table-id")

    client = FeishuClient()
    print(f"解析 wiki 节点 {wiki_token} ...")
    app_token = client.resolve_wiki_bitable(wiki_token)
    print(f"Bitable app_token: {app_token} | table: {table_id} | view: {view_id or '(默认)'}")

    records = client.list_records(app_token, table_id, view_id)
    print(f"共拉取 {len(records)} 条记录")

    rows = [parse_record(client.flatten_bitable_record(r)) for r in records]
    if args.year:
        for row in rows:
            for k in ("开船时间", "预计到港时间"):
                if row[k] is not None:
                    row[k] = row[k].replace(year=args.year)
    today = date.today()
    buffer = timedelta(days=args.arrival_buffer)

    enriched = []
    for row in rows:
        arrival = row["预计到港时间"]
        if arrival:
            row["上架时间"] = arrival + buffer
            row["到港时间来源"] = "表格"
        else:
            # 表格没有预计到港时间 → 原有策略兜底（今天+海运时效+上架缓冲），不当作已到港忽略
            row["上架时间"] = original_strategy_arrival(today, args.arrival_buffer)
            row["到港时间来源"] = "原有策略兜底"
        row["状态"] = "在途" if row["上架时间"] and row["上架时间"] >= today else "已到港/已过"
        enriched.append(row)

    # 按 ASIN 汇总（在途）
    by_asin: dict[str, dict] = {}
    for row in enriched:
        if not row["ASIN"]:
            continue
        agg = by_asin.setdefault(row["ASIN"], {
            "ASIN": row["ASIN"], "品名": row["品名"] or "",
            "在途合计": 0, "货件数": 0, "渠道": set(),
            "最早预计到港": None, "上架时间": None, "最晚上架时间": None, "待查验": 0,
            "表格到港数": 0, "兜底数": 0,
        })
        if row["状态"] == "在途":
            agg["在途合计"] += row["发货量"]
            agg["货件数"] += 1
            if row["到港时间来源"] == "表格":
                agg["表格到港数"] += 1
            else:
                agg["兜底数"] += 1
            if row["物流渠道"]:
                agg["渠道"].add(row["物流渠道"])
            if row["预计到港时间"]:
                if agg["最早预计到港"] is None or row["预计到港时间"] < agg["最早预计到港"]:
                    agg["最早预计到港"] = row["预计到港时间"]
            if row["上架时间"]:
                if agg["上架时间"] is None or row["上架时间"] < agg["上架时间"]:
                    agg["上架时间"] = row["上架时间"]
                if agg["最晚上架时间"] is None or row["上架时间"] > agg["最晚上架时间"]:
                    agg["最晚上架时间"] = row["上架时间"]
            if "查验" in row["是否查验"]:
                agg["待查验"] += 1

    summary = []
    for asin, agg in sorted(by_asin.items()):
        if args.all or (agg["在途合计"] > 0 and agg["上架时间"] and agg["上架时间"] >= today):
            summary.append({
                "ASIN": asin,
                "品名": agg["品名"],
                "在途合计": agg["在途合计"],
                "货件数": agg["货件数"],
                "渠道": "、".join(sorted(agg["渠道"])),
                "最早预计到港": agg["最早预计到港"],
                "上架时间": agg["上架时间"],
                "最晚上架时间": agg["最晚上架时间"],
                "待查验": agg["待查验"],
                "到港时间": f"表格{agg['表格到港数']}货件/兜底{agg['兜底数']}货件",
            })

    # ── 控制台输出 ──
    headers = ["ASIN", "品名", "货件单号", "发货量", "物流渠道", "跟踪编号",
               "开船时间", "预计到港时间", "上架时间", "到港时间来源", "是否查验", "数据期间", "状态"]
    print("\n================ 明细（全部记录） ================")
    print(" | ".join(headers))
    for r in enriched:
        print(" | ".join([
            r["ASIN"], r["品名"], r["货件单号"], str(r["发货量"]), r["物流渠道"], r["跟踪编号"],
            str(r["开船时间"] or ""), str(r["预计到港时间"] or ""), str(r["上架时间"] or ""),
            r["到港时间来源"], r["是否查验"], r["数据期间"], r["状态"],
        ]))

    print("\n================ 在途商品上架时间（按 ASIN 汇总，缓冲 %d 天） ================" % args.arrival_buffer)
    sheaders = ["ASIN", "品名", "在途合计", "货件数", "渠道", "最早预计到港", "上架时间", "最晚上架时间", "待查验", "到港时间"]
    print(" | ".join(sheaders))
    for s in summary:
        print(" | ".join([
            s["ASIN"], s["品名"], str(s["在途合计"]), str(s["货件数"]), s["渠道"],
            str(s["最早预计到港"] or ""), str(s["上架时间"] or ""), str(s["最晚上架时间"] or ""),
            str(s["待查验"]), s["到港时间"],
        ]))

    # ── 落盘 ──
    os.makedirs(os.path.dirname(args.csv) or ".", exist_ok=True)
    with open(args.csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(headers)
        for r in enriched:
            w.writerow([r[h].isoformat() if isinstance(r[h], date) else r[h] for h in headers])
    print(f"\nCSV 已写入: {args.csv}")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump({"summary": summary, "detail": enriched}, f, ensure_ascii=False, indent=1, default=str)
        print(f"JSON 已写入: {args.json_out}")


if __name__ == "__main__":
    main()
