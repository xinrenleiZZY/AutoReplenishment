"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, pollJob, type AnalysisSummary, type BatchStats } from "@/lib/api";

const LEVELS = ["S", "A", "B", "C", "D"];

function fmt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "-";
  return Number(n).toLocaleString();
}

function DistributionTable({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  if (entries.length === 0) return null;
  return (
    <div className="card">
      <h3 className="text-sm font-semibold mb-2">{title}</h3>
      <div className="space-y-1">
        {entries.map(([k, v]) => (
          <div key={k} className="flex items-center justify-between text-xs">
            <span>{k}</span>
            <span className="font-mono">{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function AnalysisPage() {
  const router = useRouter();
  const [level, setLevel] = useState("S");
  const [data, setData] = useState<AnalysisSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [batchStats, setBatchStats] = useState<BatchStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [analyzing, setAnalyzing] = useState<Record<string, boolean>>({});

  const load = useCallback(() => {
    setLoading(true);
    api.analysis
      .summary(level)
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [level]);

  useEffect(() => { load(); }, [load]);

  const analyzeLevel = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await api.calculation.triggerLevel(level);
      const job = await pollJob(res.job_id);
      if (job.status === "failed") throw new Error(job.error || "计算失败");
      setBatchStats(job.stats);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "计算失败");
    } finally {
      setRunning(false);
    }
  };

  const analyzeOne = async (asin: string) => {
    setAnalyzing(prev => ({ ...prev, [asin]: true }));
    setError(null);
    try {
      const res = await api.calculation.trigger(asin);
      const job = await pollJob(res.job_id);
      if (job.status === "failed") throw new Error(job.error || "分析失败");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "计算失败");
    } finally {
      setAnalyzing(prev => ({ ...prev, [asin]: false }));
    }
  };

  const ov = data?.overview;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h1 className="text-2xl font-bold">商品分析报告</h1>
        <div className="flex gap-2">
          {LEVELS.map(l => (
            <button
              key={l}
              onClick={() => { setLevel(l); setBatchStats(null); }}
              className="px-4 py-1.5 rounded-md text-sm font-medium"
              style={{
                backgroundColor: level === l ? "var(--accent-blue)" : "var(--bg-tertiary)",
                color: level === l ? "#fff" : "var(--text-primary)",
              }}
            >
              {l} 级
            </button>
          ))}
          <button
            onClick={analyzeLevel}
            disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-green)" }}
          >
            {running ? "计算中..." : `立即分析${level}级`}
          </button>
        </div>
      </div>

      {data && (
        <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
          {level} 级共 <strong>{data.total}</strong> 个在售商品 ｜ 销量快照：{data.snapshot_date ?? "-"} ｜ 库存快照：{data.inventory_date ?? "-"}
        </p>
      )}
      {error && <div className="card mb-4 text-sm" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>{error}</div>}
      {batchStats && (
        <div className="card mb-4 text-sm" style={{ borderLeft: "4px solid var(--accent-green)" }}>
          计算完成：成功 <strong>{batchStats.success}</strong> / 失败 <strong>{batchStats.failed}</strong>，
          🛒 立即采购 <strong>{batchStats.immediate}</strong> ｜ 👀 观察 <strong>{batchStats.observe}</strong> ｜ ⏸ 暂停 <strong>{batchStats.pause}</strong>
        </div>
      )}

      {loading && <p>加载中...</p>}
      {!loading && data && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
            {[
              ["商品数", data.total, "inherit"],
              ["30天总销量", fmt(ov?.vol30_total), "inherit"],
              ["平均30天", fmt(ov?.vol30_avg), "inherit"],
              ["平均评分", ov?.avg_score ?? "-", "var(--accent-blue)"],
              ["断货风险", fmt(ov?.stockout_risk), "var(--accent-red)"],
              ["天数=0", fmt(ov?.days0), "var(--accent-orange)"],
              ["积压>90天", fmt(ov?.overstock), "var(--accent-orange)"],
            ].map(([label, val, color]) => (
              <div key={label as string} className="stat-card" style={{ borderLeft: `4px solid ${color as string}` }}>
                <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>{label}</p>
                <p className="text-2xl font-bold mt-1" style={{ color: color as string }}>{val}</p>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <DistributionTable title="生命周期" data={data.distributions.life_cycle} />
            <DistributionTable title="产品类型" data={data.distributions.product_type} />
            <DistributionTable title="节日分布" data={data.distributions.festival} />
            <DistributionTable title="计算级别" data={data.distributions.calc_level} />
          </div>

          {data.top_sales.length > 0 && (
            <div className="card mb-6">
              <h2 className="text-lg font-semibold mb-3">📈 30天销量 TOP10</h2>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <th className="text-left py-2 pr-3">ASIN</th>
                    <th className="text-left py-2 pr-3">品名</th>
                    <th className="text-center py-2 pr-3">生命周期</th>
                    <th className="text-right py-2 pr-3">30天销量</th>
                    <th className="text-right py-2 pr-3">30天广告费</th>
                    <th className="text-right py-2 pr-3">广告占比</th>
                    <th className="text-right py-2 pr-3">利润率</th>
                    <th className="text-right py-2 pr-3">可用库存</th>
                    <th className="text-right py-2 pr-3">库存天数</th>
                    <th className="text-right py-2 pr-3">评分</th>
                    <th className="text-center py-2">操作</th>
                  </tr></thead>
                  <tbody>
                    {data.top_sales.map(item => (
                      <tr key={item.asin} style={{ borderBottom: "1px solid var(--border-color)" }}
                        onClick={() => router.push(`/calculation/${item.asin}`)} className="cursor-pointer">
                        <td className="py-2 pr-3 font-mono text-xs">{item.asin}</td>
                        <td className="py-2 pr-3 truncate max-w-xs">{item.name}</td>
                        <td className="py-2 pr-3 text-center">{item.life_cycle ?? "-"}</td>
                        <td className="py-2 pr-3 text-right font-mono">{fmt(item.vol30)}</td>
                        <td className="py-2 pr-3 text-right">{item.thirty_spend != null ? `$${item.thirty_spend.toFixed(2)}` : "-"}</td>
                        <td className="py-2 pr-3 text-right">{item.ad_spend_ratio != null ? `${item.ad_spend_ratio}%` : "-"}</td>
                        <td className="py-2 pr-3 text-right">
                          {item.profit_rate != null ? `${Math.round(item.profit_rate * 100)}%`
                            : item.est_profit_rate != null ? `${Math.round(item.est_profit_rate * 100)}%（估）`
                            : "-"}
                        </td>
                        <td className="py-2 pr-3 text-right">{fmt(item.avail)}</td>
                        <td className="py-2 pr-3 text-right">{fmt(item.inv_days)}</td>
                        <td className="py-2 pr-3 text-right">{item.score ?? "-"}</td>
                        <td className="py-2 text-center">
                          <button
                            onClick={e => { e.stopPropagation(); analyzeOne(item.asin); }}
                            disabled={analyzing[item.asin]}
                            className="px-2 py-1 rounded text-xs font-medium text-white"
                            style={{ backgroundColor: analyzing[item.asin] ? "#94a3b8" : "var(--accent-green)" }}
                          >
                            {analyzing[item.asin] ? "分析中" : "立即分析"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {(data.risks.stockout.length > 0 || data.risks.overstock.length > 0) && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {data.risks.stockout.length > 0 && (
                <div className="card" style={{ borderTop: "3px solid var(--accent-red)" }}>
                  <h2 className="text-sm font-semibold mb-3">🚨 断货风险（{data.risks.stockout.length}）</h2>
                  <ul className="space-y-1.5">
                    {data.risks.stockout.slice(0, 10).map(item => (
                      <li key={item.asin} className="text-xs">
                        <button className="w-full text-left" onClick={() => router.push(`/products/${item.asin}`)}>
                          <span className="font-mono">{item.asin}</span> {item.name}
                          <span className="ml-2" style={{ color: "var(--accent-red)" }}>库存 {fmt(item.inv_days)}天 / 周期{fmt(item.replenish_cycle)}天</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {data.risks.overstock.length > 0 && (
                <div className="card" style={{ borderTop: "3px solid var(--accent-orange)" }}>
                  <h2 className="text-sm font-semibold mb-3">📦 库存积压（{data.risks.overstock.length}）</h2>
                  <ul className="space-y-1.5">
                    {data.risks.overstock.slice(0, 10).map(item => (
                      <li key={item.asin} className="text-xs">
                        <button className="w-full text-left" onClick={() => router.push(`/products/${item.asin}`)}>
                          <span className="font-mono">{item.asin}</span> {item.name}
                          <span className="ml-2" style={{ color: "var(--accent-orange)" }}>库存 {fmt(item.inv_days)}天</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
