"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type SeasonalCurve } from "@/lib/api";

interface MonthPoint { month: string; pct: number }

function parseDistribution(raw: string): MonthPoint[] {
  try {
    const obj = JSON.parse(raw) as Record<string, number>;
    return Object.entries(obj)
      .map(([month, pct]) => ({ month, pct: Number(pct) }))
      .sort((a, b) => a.month.localeCompare(b.month));
  } catch {
    return [];
  }
}

export default function SeasonalCurvesPage() {
  const [curves, setCurves] = useState<SeasonalCurve[]>([]);
  const [loading, setLoading] = useState(true);
  const [festival, setFestival] = useState("");

  useEffect(() => {
    api.seasonalCurves
      .list(festival || undefined)
      .then(setCurves)
      .catch(() => setCurves([]))
      .finally(() => setLoading(false));
  }, [festival]);

  const festivals = useMemo(() => [...new Set(curves.map(c => c.festival))], [curves]);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold">季节销售曲线</h1>
        <select value={festival} onChange={e => setFestival(e.target.value)}
          className="text-sm px-3 py-1.5 rounded-md border"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}>
          <option value="">全部节日</option>
          {festivals.map(f => <option key={f} value={f}>{f}</option>)}
        </select>
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        用于新品销量预测的销售占比曲线模板。{loading ? "加载中..." : `共 ${curves.length} 条曲线`}
      </p>
      {!loading && curves.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无季节曲线数据</div>}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        {curves.map(curve => {
          const points = parseDistribution(curve.month_distribution);
          return (
            <div key={curve.id} className="card">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-lg font-semibold">{curve.festival} · {curve.sub_category}</h2>
                <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                  样本 {curve.sample_count}
                </span>
              </div>
              {points.length === 0 && <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>分布数据为空</p>}
              <div className="space-y-1.5">
                {points.map(({ month, pct }) => (
                  <div key={month} className="flex items-center gap-2 text-sm">
                    <span className="w-8">{Number(month)}月</span>
                    <div className="flex-1 h-4 rounded" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                      <div className="h-4 rounded" style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: "var(--accent-blue)" }} />
                    </div>
                    <span className="w-12 text-right font-mono">{pct.toFixed(1)}%</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
