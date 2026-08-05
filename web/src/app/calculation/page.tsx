"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type BatchStats, type CalculationResult, type DueStats } from "@/lib/api";

export default function CalculationPage() {
  const router = useRouter();
  const [results, setResults] = useState<CalculationResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [filter, setFilter] = useState("");
  const [batchStats, setBatchStats] = useState<BatchStats | null>(null);
  const [dueStats, setDueStats] = useState<DueStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadResults = useCallback(() => {
    setLoading(true);
    api.calculation
      .results(filter ? { purchase_level: filter, limit: 200 } : { limit: 200 })
      .then(setResults)
      .catch((e: Error) => {
        setResults([]);
        setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [filter]);

  useEffect(() => { loadResults(); }, [loadResults]);

  useEffect(() => {
    api.calculation.dueStats().then(setDueStats).catch(() => setDueStats(null));
  }, []);

  const triggerBatch = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await api.calculation.triggerBatch();
      setBatchStats(res.stats);
      loadResults();
    } catch (e) {
      setError(e instanceof Error ? e.message : "批量计算失败");
    } finally {
      setRunning(false);
    }
  };

  const triggerDue = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await api.calculation.triggerDue();
      setBatchStats(res.stats);
      api.calculation.dueStats().then(setDueStats).catch(() => null);
      loadResults();
    } catch (e) {
      setError(e instanceof Error ? e.message : "按频率计算失败");
    } finally {
      setRunning(false);
    }
  };

  const levelOrder = ["S", "A", "B", "C", "D"];
  const levelSummary = dueStats
    ? levelOrder
        .filter(lv => dueStats.by_level[lv])
        .map(lv => {
          const info = dueStats.by_level[lv];
          return `${lv}级 ${info.due}/${info.total}（${info.frequency_days}天）`;
        })
        .join(" ｜ ")
    : "";

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
          <button onClick={triggerDue} disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-green)" }}>
            {running ? "计算中..." : "按频率计算"}
          </button>
          <button onClick={triggerBatch} disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-blue)" }}>
            全量重算
          </button>
        </div>
      </div>

      {dueStats && (
        <div className="card mb-4 text-sm">
          <p style={{ color: "var(--text-tertiary)" }}>
            今日到期 <strong>{dueStats.due}</strong> / {dueStats.total} 个产品
          </p>
          {levelSummary && <p className="mt-1" style={{ color: "var(--text-secondary)" }}>{levelSummary}</p>}
        </div>
      )}

      {error && <div className="card mb-4" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>{error}</div>}
      {batchStats && (
        <div className="card mb-4 text-sm" style={{ borderLeft: "4px solid var(--accent-green)" }}>
          计算完成：到期 <strong>{batchStats.due ?? batchStats.total}</strong> / 跳过 <strong>{batchStats.skipped ?? 0}</strong>，
          成功 <strong>{batchStats.success}</strong> / 失败 <strong>{batchStats.failed}</strong>，
          🛒 立即采购 <strong>{batchStats.immediate}</strong> ｜ 👀 观察 <strong>{batchStats.observe}</strong> ｜ ⏸ 暂停 <strong>{batchStats.pause}</strong>
        </div>
      )}

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
                onClick={() => router.push(`/calculation/${r.asin}`)} className="cursor-pointer"
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
