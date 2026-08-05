"use client";

import { useEffect, useState } from "react";
import { api, type CalculationResult } from "@/lib/api";

export default function InventoryPage() {
  const [results, setResults] = useState<CalculationResult[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.calculation
      .results({ limit: 200 })
      .then(setResults)
      .catch(() => setResults([]))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">库存健康分析</h1>
      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-2">库存紧急程度</h2>
        <div className="grid grid-cols-4 gap-3 text-sm">
          <div className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}><span className="font-bold" style={{ color: "var(--accent-red)" }}>危险</span><p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>库存&lt;15天</p></div>
          <div className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}><span className="font-bold" style={{ color: "var(--accent-orange)" }}>偏低</span><p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>15-30天</p></div>
          <div className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}><span className="font-bold" style={{ color: "var(--accent-green)" }}>健康</span><p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>30-90天</p></div>
          <div className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}><span className="font-bold" style={{ color: "var(--text-tertiary)" }}>过量</span><p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>91天+</p></div>
        </div>
      </div>
      {loading && <p>加载中...</p>}
      {results.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">ASIN</th><th className="text-right py-2 pr-3">可用库存</th>
              <th className="text-right py-2 pr-3">库存天数</th><th className="text-right py-2 pr-3">补货周期</th>
              <th className="text-center py-2 pr-3">紧急评分</th><th className="text-center py-2">触发</th>
            </tr></thead>
            <tbody>{results.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                <td className="py-2 pr-3 font-mono text-xs">{r.asin}</td>
                <td className="py-2 pr-3 text-right">{r.available_stock?.toLocaleString() ?? "-"}</td>
                <td className="py-2 pr-3 text-right font-bold" style={{ color: (r.inventory_days ?? 999) < 15 ? "var(--accent-red)" : (r.inventory_days ?? 999) < 30 ? "var(--accent-orange)" : "inherit" }}>{r.inventory_days ?? "-"}</td>
                <td className="py-2 pr-3 text-right">{r.replenishment_cycle ?? "-"}</td>
                <td className="py-2 pr-3 text-center">{r.urgency_score ?? "-"}</td>
                <td className="py-2 text-center text-xs">{r.purchase_trigger ?? "-"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
