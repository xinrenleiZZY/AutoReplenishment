"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type DailyReport, type NextCalculation, type RiskReport } from "@/lib/api";
import WatermarkBanner from "@/components/WatermarkBanner";

export default function DashboardPage() {
  const router = useRouter();
  const [report, setReport] = useState<DailyReport | null>(null);
  const [risks, setRisks] = useState<RiskReport | null>(null);
  const [productTotal, setProductTotal] = useState<number | null>(null);
  const [overstockDays, setOverstockDays] = useState<number | null>(null);
  const [fxRate, setFxRate] = useState<number | null>(null);
  const [fxUpdatedAt, setFxUpdatedAt] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [nextCalc, setNextCalc] = useState<NextCalculation | null>(null);
  const [now, setNow] = useState<Date | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [progress, setProgress] = useState<{ total: number; done: number; percent: number; current_asin: string | null } | null>(null);

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const loadNext = useCallback(() => {
    api.calculation.nextCalculation().then(setNextCalc).catch(() => null);
  }, []);

  useEffect(() => {
    Promise.all([
      api.calculation.dailyReport(false),
      api.calculation.risks(),
      api.products.lifecycleStats().then(s => s.total).catch(() => null),
      api.config.list()
        .then(list => {
          setOverstockDays(Number(list.find(p => p.key === "inventory_healthy_max_days")?.value ?? 90));
          const fx = list.find(p => p.key === "usd_cny_rate")?.value;
          setFxRate(fx ? Number(fx) : null);
          const fxAt = list.find(p => p.key === "fx_updated_at")?.value;
          setFxUpdatedAt(fxAt != null ? String(fxAt) : null);
          return null;
        })
        .catch(() => null),
    ])
      .then(([rep, rk, total, overstock]) => {
        setReport(rep);
        setRisks(rk);
        setProductTotal(total);
        setOverstockDays(overstock);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    loadNext();
  }, [loadNext]);

  const analyzeNow = async () => {
    setAnalyzing(true);
    setError(null);
    setProgress({ total: 0, done: 0, percent: 0, current_asin: null });
    try {
      const res = await api.calculation.triggerDue();
      // 轮询进度直到完成
      for (;;) {
        const job = await api.calculation.getJob(res.job_id);
        if (job.progress) setProgress(job.progress);
        if (job.status !== "running") break;
        await new Promise(r => setTimeout(r, 2000));
      }
      // 完成后刷新预告与日报
      loadNext();
      api.calculation.dailyReport(false).then(setReport).catch(() => null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "分析失败");
    } finally {
      setAnalyzing(false);
      setProgress(null);
    }
  };

  const stats = [
    { label: "总 ASIN", val: productTotal ?? report?.total_asins, color: "inherit" },
    { label: "立即采购", val: report?.immediate_count, color: "var(--accent-orange)" },
    { label: "观察", val: report?.observe_count, color: "var(--accent-blue)" },
    { label: "暂停", val: report?.pause_count, color: "var(--accent-green)" },
  ];

  const timeText = now
    ? `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}`
    : "--:--:--";
  const dateText = now
    ? `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`
    : "----";

  return (
    <div>
      <WatermarkBanner title="仪表盘" subtitle="✦ 自动补货决策 · 实时运行态势" artistic marquee={false} />
      {error && !report && (
        <div className="card mb-6" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>
          数据加载失败：{error}
        </div>
      )}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-6 gap-4 mb-8">
        {stats.map(s => (
          <div key={s.label} className="stat-card" style={{ borderLeft: `4px solid ${s.color}` }}>
            <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>{s.label}</p>
            <p className="text-3xl font-bold mt-1" style={{ color: s.color }}>{s.val ?? "-"}</p>
          </div>
        ))}

        {/* 卡片：当前系统时间 */}
        <div className="stat-card flex flex-col items-center justify-center" style={{ borderLeft: "4px solid var(--accent-blue)" }}>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>当前时间</p>
          <p className="text-2xl font-bold font-mono mt-1">{timeText}</p>
          <p className="text-xs font-mono mt-0.5" style={{ color: "var(--text-tertiary)" }}>{dateText}</p>
        </div>

        {/* 卡片：下次分析等级 + 立即分析（两行紧凑版） */}
        <div className="stat-card flex flex-col justify-center" style={{ borderLeft: "4px solid var(--accent-orange)" }}>
          <p className="text-sm font-medium whitespace-nowrap">
            <span style={{ color: "var(--text-tertiary)" }}>下次分析等级：</span>
            <span className="font-bold" style={{ color: "var(--accent-orange)" }}>
              {nextCalc?.next_levels_text ?? "-"}
            </span>
          </p>
          <div className="flex items-center gap-2 mt-1 whitespace-nowrap">
            <span className="text-xs font-mono" style={{ color: "var(--text-tertiary)" }}>
              {nextCalc?.next_date ? (nextCalc.next_date === nextCalc.today ? "今天到期" : nextCalc.next_date) : "-"}
            </span>
            <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>|</span>
            <button
              onClick={analyzeNow}
              disabled={analyzing}
              className="px-2 py-0.5 rounded-md text-xs font-medium text-white"
              style={{ backgroundColor: analyzing ? "#94a3b8" : "var(--accent-green)" }}
            >
              {analyzing ? "分析中..." : "立即分析"}
            </button>
          </div>
          {progress && progress.total > 0 && (
            <div style={{ width: "100%", paddingTop: "8px" }}>
              <div className="flex justify-between text-[10px] mb-1" style={{ color: "var(--text-tertiary)" }}>
                <span>{progress.done}/{progress.total}</span>
                <span className="font-mono">{progress.percent}%</span>
              </div>
              <div style={{ height: 6, borderRadius: 3, backgroundColor: "var(--bg-tertiary)", overflow: "hidden" }}>
                <div style={{ height: "100%", width: `${progress.percent}%`, backgroundColor: "var(--accent-green)", transition: "width .5s" }} />
              </div>
              {progress.current_asin && (
                <p className="text-[10px] font-mono mt-1 truncate" style={{ color: "var(--text-tertiary)" }}>
                  正在分析: {progress.current_asin}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {fxRate != null && (
        <div className="card mb-8 flex items-center justify-between">
          <div>
            <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>实时汇率</p>
            <p className="text-2xl font-bold" style={{ color: "var(--accent-blue)" }}>USD/CNY = {fxRate.toFixed(4)}</p>
          </div>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            更新时间：{fxUpdatedAt || "-"}（每日 02:00 自动同步 Google Finance，成本表同步使用）
          </p>
        </div>
      )}

      <div className="card">
        <h2 className="text-lg font-semibold mb-4">⚠️ 重点提醒</h2>
        {loading && <p style={{ color: "var(--text-tertiary)" }}>加载中...</p>}
        {!loading && !report && <p style={{ color: "var(--text-tertiary)" }}>暂无计算结果，请先运行批量计算。</p>}
        {report && report.top_alerts?.length > 0 && (
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-4">ASIN</th><th className="text-left py-2 pr-4">产品名称</th>
              <th className="text-center py-2 pr-4">提醒</th>
              <th className="text-right py-2 pr-4">评分</th><th className="text-right py-2">建议数量</th>
            </tr></thead>
            <tbody>{report.top_alerts.map(item => (
              <tr key={item.asin} style={{ borderBottom: "1px solid var(--border-color)" }}
                onClick={() => router.push(`/products/${item.asin}`)} className="cursor-pointer"
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}>
                <td className="py-2 pr-4 font-mono">{item.asin}</td>
                <td className="py-2 pr-4 truncate max-w-xs">
                  {item.product_name}
                  {item.alert_reason && <p className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>{item.alert_reason}</p>}
                </td>
                <td className="py-2 pr-4 text-center">
                  {item.alert_type && <span className="px-1.5 py-0.5 rounded text-[10px] font-medium"
                    style={{ backgroundColor: item.alert_type === "断货风险" ? "#fee2e2" : "#f1f5f9", color: item.alert_type === "断货风险" ? "#dc2626" : "#64748b" }}>{item.alert_type}</span>}
                </td>
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
              { key: "overstock" as const, label: "库存积压", color: "var(--accent-orange)", desc: `库存覆盖天数 > ${overstockDays ?? 90}天` },
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
