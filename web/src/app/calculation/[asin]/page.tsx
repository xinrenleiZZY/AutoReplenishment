"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

export default function CalculationDetailPage() {
  const params = useParams();
  const asin = params.asin as string;
  const [result, setResult] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`/api/v1/calculation/results/${asin}/latest`)
      .then(r => r.json().catch(() => null)).then(setResult).finally(() => setLoading(false));
  }, [asin]);

  if (loading) return <p>加载中...</p>;
  if (!result?.id) return <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无计算结果，<Link href={`/products/${asin}`} style={{ color: "var(--accent-blue)" }}>返回产品页</Link></div>;

  let scoreDetail: Record<string, any> = {};
  try { scoreDetail = JSON.parse(result.score_detail || "{}"); } catch {}

  return (
    <div>
      <Link href="/calculation" className="text-sm mb-4 inline-block" style={{ color: "var(--accent-blue)" }}>← 返回列表</Link>
      <h1 className="text-xl font-bold mb-1">{asin}</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>计算日期：{result.calc_date}</p>

      <div className="card mb-6 text-center py-8" style={{ borderLeft: `6px solid ${(result.purchase_score ?? 0) >= 80 ? "#fef3c7" : "#f1f5f9"}` }}>
        <p className="text-sm mb-1" style={{ color: "var(--text-tertiary)" }}>采购评分</p>
        <p className="text-5xl font-bold mb-2" style={{ color: (result.purchase_score ?? 0) >= 80 ? "#92400e" : "#475569" }}>{result.purchase_score}</p>
        <span className="px-3 py-1 rounded text-sm font-medium"
          style={{ backgroundColor: result.purchase_level === "立即采购" ? "#fef3c7" : result.purchase_level === "观察" ? "#dbeafe" : "#f1f5f9" }}>
          {result.purchase_level}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">评分明细</h2>
          {Object.entries(scoreDetail).length === 0 && <p style={{ color: "var(--text-tertiary)" }}>暂无评分明细</p>}
          <div className="space-y-4">
            {Object.entries(scoreDetail).map(([key, detail]: [string, any]) => (
              <div key={key}>
                <div className="flex justify-between text-sm mb-1">
                  <span>{detail.label || key}</span>
                  <span className="font-medium">{detail.value}分 × {((detail.weight || 0) * 100).toFixed(0)}%</span>
                </div>
                <div className="w-full h-2 rounded-full" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                  <div className="h-2 rounded-full transition-all" style={{ width: `${Math.min(detail.value, 100)}%`, backgroundColor: detail.value >= 80 ? "var(--accent-green)" : detail.value >= 60 ? "var(--accent-orange)" : "var(--accent-red)" }} />
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold mb-4">决策数据</h2>
          {[
            ["采购触发", result.purchase_trigger],
            ["建议采购数量", result.suggested_qty?.toLocaleString()],
            ["库存覆盖天数", result.inventory_days + " 天"],
            ["补货周期", result.replenishment_cycle + " 天"],
            ["预测总销量", result.forecast_total?.toLocaleString()],
            ["紧急程度评分", result.urgency_score],
          ].map(([label, value]) => (
            <div key={label} className="flex justify-between py-2 text-sm" style={{ borderBottom: "1px solid var(--border-color)" }}>
              <span style={{ color: "var(--text-tertiary)" }}>{label}</span>
              <span className="font-bold">{value ?? "-"}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
