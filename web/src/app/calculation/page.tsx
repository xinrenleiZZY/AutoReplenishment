"use client";

import { useEffect, useState } from "react";

export default function CalculationPage() {
  const [results, setResults] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState("");

  const loadResults = () => {
    setLoading(true);
    const url = filter ? `/api/v1/calculation/results?purchase_level=${filter}` : "/api/v1/calculation/results";
    fetch(url).then(r => r.json()).then(setResults).catch(() => setResults([])).finally(() => setLoading(false));
  };

  useEffect(() => { loadResults(); }, [filter]);

  const triggerBatch = async () => {
    setRunning(true);
    await fetch("/api/v1/calculation/trigger/batch", { method: "POST" });
    alert("批量计算已触发");
    setRunning(false);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">计算结果</h1>
        <div className="flex gap-3 items-center">
          <select value={filter} onChange={e => setFilter(e.target.value)}
            className="text-sm px-3 py-1.5 rounded-md border"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
            <option value="">全部级别</option>
            <option value="立即采购">立即采购</option>
            <option value="观察">观察</option>
            <option value="暂停">暂停</option>
          </select>
          <button onClick={triggerBatch} disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-blue)" }}>
            {running ? "计算中..." : "触发批量计算"}
          </button>
        </div>
      </div>
      {loading && <p>加载中...</p>}
      {!loading && results.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无计算结果</div>}
      {results.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">ASIN</th><th className="text-center py-2 pr-3">评分</th>
              <th className="text-center py-2 pr-3">级别</th><th className="text-right py-2 pr-3">建议数量</th>
              <th className="text-right py-2 pr-3">库存天数</th><th className="text-center py-2">触发</th>
            </tr></thead>
            <tbody>{results.map(r => (
              <tr key={r.id} style={{ borderBottom: "1px solid var(--border-color)" }}
                onClick={() => window.location.href = `/calculation/${r.asin}`} className="cursor-pointer"
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}>
                <td className="py-2 pr-3 font-mono text-xs">{r.asin}</td>
                <td className="py-2 pr-3 text-center font-bold">{r.purchase_score ?? "-"}</td>
                <td className="py-2 pr-3 text-center">
                  <span className="px-2 py-0.5 rounded text-xs font-medium"
                    style={{ backgroundColor: r.purchase_level === "立即采购" ? "#fef3c7" : r.purchase_level === "观察" ? "#dbeafe" : "#f1f5f9" }}>
                    {r.purchase_level}
                  </span>
                </td>
                <td className="py-2 pr-3 text-right font-mono">{r.suggested_qty?.toLocaleString() ?? "-"}</td>
                <td className="py-2 pr-3 text-right">{r.inventory_days ?? "-"}</td>
                <td className="py-2 text-center text-xs">{r.purchase_trigger ?? "-"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
