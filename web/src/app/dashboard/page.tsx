"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type DailyReport, type RiskReport } from "@/lib/api";

export default function DashboardPage() {
  const router = useRouter();
  const [report, setReport] = useState<DailyReport | null>(null);
  const [risks, setRisks] = useState<RiskReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.calculation.dailyReport(false),
      api.calculation.risks(),
    ])
      .then(([rep, rk]) => {
        setReport(rep);
        setRisks(rk);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const stats = [
    { label: "总 ASIN", val: report?.total_asins, color: "inherit" },
    { label: "立即采购", val: report?.immediate_count, color: "var(--accent-orange)" },
    { label: "观察", val: report?.observe_count, color: "var(--accent-blue)" },
    { label: "暂停", val: report?.pause_count, color: "var(--accent-green)" },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">仪表盘</h1>
      {error && !report && (
        <div className="card mb-6" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>
          数据加载失败：{error}
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {stats.map(s => (
          <div key={s.label} className="stat-card" style={{ borderLeft: `4px solid ${s.color}` }}>
            <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
            <p className="text-3xl font-bold mt-1" style={{ color: s.color }}>{s.val ?? "-"}</p>
          </div>
        ))}
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold mb-4">⚠️ 重点提醒</h2>
        {loading && <p style={{ color: "var(--text-tertiary)" }}>加载中...</p>}
        {!loading && !report && <p style={{ color: "var(--text-tertiary)" }}>暂无计算结果，请先运行批量计算。</p>}
        {report && report.top_alerts?.length > 0 && (
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-4">ASIN</th><th className="text-left py-2 pr-4">产品名称</th>
              <th className="text-right py-2 pr-4">评分</th><th className="text-right py-2">建议数量</th>
            </tr></thead>
            <tbody>{report.top_alerts.map(item => (
              <tr key={item.asin} style={{ borderBottom: "1px solid var(--border-color)" }}
                onClick={() => router.push(`/products/${item.asin}`)} className="cursor-pointer"
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}>
                <td className="py-2 pr-4 font-mono">{item.asin}</td>
                <td className="py-2 pr-4 truncate max-w-xs">{item.product_name}</td>
                <td className="py-2 pr-4 text-right"><span className="px-2 py-0.5 rounded text-xs font-medium"
                  style={{ backgroundColor: (item.purchase_score ?? 0) >= 80 ? "#fef3c7" : "#f1f5f9" }}>{item.purchase_score}</span></td>
                <td className="py-2 text-right font-mono">{item.suggested_qty?.toLocaleString()}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>

      <div className="mt-8">
        <h2 className="text-lg font-semibold mb-4">🚨 实时风险提醒</h2>
        {!risks && <p style={{ color: "var(--text-tertiary)" }}>加载中...</p>}
        {risks && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
            {[
              { key: "stockout" as const, label: "断货风险", color: "var(--accent-red)", desc: "库存覆盖天数 < 补货周期" },
              { key: "overstock" as const, label: "库存积压", color: "var(--accent-orange)", desc: "库存覆盖天数 > 90天" },
              { key: "profit" as const, label: "利润风险", color: "var(--accent-blue)", desc: "产品利润率为负" },
            ].map(box => (
              <div key={box.key} className="card" style={{ borderTop: `3px solid ${box.color}` }}>
                <div className="flex items-center justify-between mb-1">
                  <h3 className="font-semibold text-sm">{box.label}</h3>
                  <span className="text-2xl font-bold" style={{ color: box.color }}>{risks[box.key].length}</span>
                </div>
                <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>{box.desc}</p>
                {risks[box.key].length === 0 && <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>暂无风险</p>}
                <ul className="space-y-2">
                  {risks[box.key].slice(0, 5).map(item => (
                    <li key={`${box.key}-${item.asin}`}>
                      <button
                        className="w-full text-left text-xs rounded-md p-2 transition-all"
                        style={{ backgroundColor: "var(--bg-tertiary)" }}
                        onClick={() => router.push(`/products/${item.asin}`)}
                      >
                        <span className="font-mono">{item.asin}</span>
                        {item.product_level && <span className="ml-1 px-1 py-0.5 rounded text-[10px] font-bold" style={{ backgroundColor: "var(--bg-secondary)" }}>{item.product_level}</span>}
                        <p className="mt-0.5" style={{ color: "var(--text-secondary)" }}>{item.reason}</p>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
