"""检查抓取的产品数据结构和字段"""
import json

# 先看全量JSON
with open(r"e:\ZY2026\yy021-自动补货决策系统\p_id\msku_id_full.json", "r", encoding="utf-8") as f:
    data = json.load(f)

print(f"全量JSON: {len(data)} 条")

first = data[0]
print(f"\n第一条全部字段 ({len(first)}个):")
for k, v in first.items():
    print(f"  {k}: {v}")

# 统计关键字段
for field in ["open_date", "total_volume", "yesterday_volume", "thirty_volume"]:
    count = sum(1 for item in data if item.get(field) is not None and item.get(field) != "")
    print(f"\n{field}有值: {count}/{len(data)}")

# 找一个有值的示例
for item in data:
    if item.get("total_volume") is not None:
        print(f"\n示例 - asin={item['asin']}")
        print(f"  open_date: {item.get('open_date')}")
        print(f"  total_volume: {item['total_volume']}")
        print(f"  yesterday_volume: {item.get('yesterday_volume')}")
        print(f"  thirty_volume: {item.get('thirty_volume')}")
        print(f"  price: {item.get('price')}")
        print(f"  fulfillment_channel_type: {item.get('fulfillment_channel_type')}")
        break
