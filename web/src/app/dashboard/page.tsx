"use client";

import { useEffect, useState } from "react";

interface DailyReport {
  calc_date: string; total_asins: number; immediate_count: number;
  observe_count: number; pause_count: number;
  top_alerts: { asin: string; product_name: string; purchase_score: number; suggested_qty: number }[];
}

export default function DashboardPage() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/v1/calculation/daily-report")
      .then(r => r.json()).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">仪表盘</h1>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        {[{ label: "总 ASIN", val: report?.total_asins, color: "inherit" },
          { label: "立即采购", val: report?.immediate_count, color: "var(--accent-orange)" },
          { label: "观察", val: report?.observe_count, color: "var(--accent-blue)" },
          { label: "暂停", val: report?.pause_count, color: "var(--accent-green)" },
        ].map(s => (
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
                onClick={() => window.location.href = `/products/${item.asin}`} className="cursor-pointer">
                <td className="py-2 pr-4 font-mono">{item.asin}</td>
                <td className="py-2 pr-4 truncate max-w-xs">{item.product_name}</td>
                <td className="py-2 pr-4 text-right"><span className="px-2 py-0.5 rounded text-xs font-medium"
                  style={{ backgroundColor: item.purchase_score >= 80 ? "#fef3c7" : "#f1f5f9" }}>{item.purchase_score}</span></td>
                <td className="py-2 text-right font-mono">{item.suggested_qty?.toLocaleString()}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </div>
  );
}
