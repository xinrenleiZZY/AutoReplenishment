import requests

r = requests.post("http://127.0.0.1:8000/api/v1/calculation/trigger/B0DP2FLFKM", timeout=60)
print("状态:", r.status_code)
data = r.json()
if "data" in data:
    d = data["data"]
    print("建议数量:", d.get("suggested_qty"), "评分:", d.get("purchase_score"),
          "等级:", d.get("purchase_level"))
    steps = d.get("steps", [])
    print("步骤数:", len(steps))
    for s in steps:
        print(f"  [{s['step_no']}] {s['step_name']}: {str(s['reason'])[:60]}")
else:
    print(r.text[:800])
