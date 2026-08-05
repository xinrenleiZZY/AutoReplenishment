"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type CategoryLeadtime } from "@/lib/api";

export default function CategoryLeadtimesPage() {
  const [items, setItems] = useState<CategoryLeadtime[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.categoryLeadtimes
      .list()
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  }, []);

  const groups = useMemo(() => {
    const map = new Map<string, CategoryLeadtime[]>();
    for (const item of items) {
      const key = item.level1_category || "未分类";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(item);
    }
    return [...map.entries()];
  }, [items]);

  const fmtLead = (item: CategoryLeadtime) => {
    if (item.lead_time_min == null && item.lead_time_max == null) return "未设置";
    if (item.lead_time_min != null && item.lead_time_max != null && item.lead_time_min === item.lead_time_max) {
      return `${item.lead_time_min} 天`;
    }
    return `${item.lead_time_min ?? "-"}-${item.lead_time_max ?? "-"} 天`;
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">产品分类工期</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>
        {loading ? "加载中..." : `共 ${items.length} 条分类工期记录`}
      </p>
      {!loading && items.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无分类工期数据</div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {groups.map(([category, list]) => (
          <div key={category} className="card">
            <h3 className="font-semibold text-sm mb-2">{category}</h3>
            <ul className="space-y-1">
              {list.map(item => (
                <li key={item.id} className="text-xs flex justify-between" style={{ color: "var(--text-secondary)" }}>
                  <span>{item.level2_category || "（未细分）"}</span>
                  <span className="font-mono">{fmtLead(item)}</span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
