"use client";

import { useEffect, useState } from "react";

export default function DailyReportPage() {
  const [report, setReport] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [pushing, setPushing] = useState(false);

  const loadReport = () => {
    setLoading(true);
    fetch("/api/v1/calculation/daily-report")
      .then(r => r.json()).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false));
  };

  useEffect(() => { loadReport(); }, []);

  const pushToFeishu = async () => {
    setPushing(true);
    await fetch("/api/v1/calculation/daily-report/push", { method: "POST" }).then(() => alert("已推送")).catch(() => alert("推送失败"));
    setPushing(false);
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">采购日报</h1>
        {report && <button onClick={pushToFeishu} disabled={pushing}
          className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
          style={{ backgroundColor: pushing ? "#94a3b8" : "var(--accent-green)" }}>{pushing ? "推送中..." : "推送飞书"}</button>}
      </div>
      {loading && <p>加载中...</p>}
      {!loading && !report && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无日报数据</div>}
      {report && (
        <>
          <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>日期：{report.calc_date}</p>
          <div className="grid grid-cols-4 gap-4 mb-6">
            {[
              ["总 ASIN", report.total_asins, ""],
              ["立即采购", report.immediate_count, "var(--accent-orange)"],
              ["观察", report.observe_count, "var(--accent-blue)"],
              ["暂停", report.pause_count, "var(--accent-green)"],
            ].map(([label, val, color]) => (
              <div className="stat-card" style={{ borderLeft: color ? `4px solid ${color}` : "" }}>
                <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>{label}</p>
                <p className="text-2xl font-bold" style={{ color: color || "inherit" }}>{val}</p>
              </div>
            ))}
          </div>
          {report.results?.length > 0 && (
            <div className="card">
              <h2 className="text-lg font-semibold mb-3">所有 ASIN 结果</h2>
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <th className="text-left py-2 pr-3">ASIN</th>
                    <th className="text-center py-2 pr-3">评分</th>
                    <th className="text-center py-2 pr-3">级别</th>
                    <th className="text-right py-2 pr-3">建议数量</th>
                    <th className="text-right py-2">库存天数</th>
                  </tr></thead>
                  <tbody>{report.results.map((r: any, i: number) => (
                    <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td className="py-1.5 pr-3 font-mono text-xs">{r.asin}</td>
                      <td className="py-1.5 pr-3 text-center">{r.purchase_score ?? "-"}</td>
                      <td className="py-1.5 pr-3 text-center">
                        <span className="px-1.5 py-0.5 rounded text-xs" style={{
                          backgroundColor: r.purchase_level === "立即采购" ? "#fef3c7" : r.purchase_level === "观察" ? "#dbeafe" : "#f1f5f9",
                        }}>{r.purchase_level}</span></td>
                      <td className="py-1.5 pr-3 text-right font-mono">{r.suggested_qty?.toLocaleString() ?? "-"}</td>
                      <td className="py-1.5 text-right">{r.inventory_days ?? "-"}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
