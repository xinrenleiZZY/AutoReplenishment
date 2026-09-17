"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type DailyReport } from "@/lib/api";

const LEVEL_STYLE: Record<string, { bg: string; color: string }> = {
  "立即采购": { bg: "#fef3c7", color: "#92400e" },
  "观察": { bg: "#dbeafe", color: "#1e40af" },
  "暂停": { bg: "#f1f5f9", color: "#64748b" },
  "终止": { bg: "#fee2e2", color: "#dc2626" },
  "未触发": { bg: "#f1f5f9", color: "#64748b" },
};

const ALERT_STYLE: Record<string, { bg: string; color: string }> = {
  "立即采购": { bg: "#fef3c7", color: "#92400e" },
  "观察": { bg: "#dbeafe", color: "#1e40af" },
  "断货风险": { bg: "#fee2e2", color: "#dc2626" },
};

export default function DailyReportPage() {
  const [report, setReport] = useState<DailyReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [pushing, setPushing] = useState(false);
  const [aiRunning, setAiRunning] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const loadReport = useCallback(() => {
    setLoading(true);
    api.calculation
      .dailyReport()
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadReport(); }, [loadReport]);

  const pushToFeishu = async () => {
    setPushing(true);
    setMessage(null);
    try {
      const res = await api.calculation.pushReport();
      setMessage({ ok: true, text: `${res.message}（${res.summary.immediate_count} 个立即采购，已@负责人）` });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "推送失败" });
    } finally {
      setPushing(false);
    }
  };

  const genAiReport = async () => {
    setAiRunning(true);
    setMessage(null);
    try {
      const enriched = await api.ai.dailyReport();
      setReport(enriched);
      setMessage({ ok: true, text: "AI 日报已生成（每条重点提醒含全面分析）" });
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "AI 日报生成失败" });
    } finally {
      setAiRunning(false);
    }
  };

  const stats = [
    ["总 ASIN", report?.total_asins, ""],
    ["立即采购", report?.immediate_count, "var(--accent-orange)"],
    ["观察", report?.observe_count, "var(--accent-blue)"],
    ["暂停", report?.pause_count, "var(--accent-green)"],
    ["断货风险", report?.stockout_count, "var(--accent-red)"],
  ] as const;

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold">采购日报</h1>
        <div className="flex gap-2">
          <button onClick={genAiReport} disabled={aiRunning}
            className="px-4 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: aiRunning ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
            {aiRunning ? "AI 生成中..." : "AI 生成日报"}
          </button>
          {report && <button onClick={pushToFeishu} disabled={pushing}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: pushing ? "#94a3b8" : "var(--accent-green)" }}>{pushing ? "推送中..." : "推送飞书（@负责人）"}</button>}
        </div>
      </div>
      {message && (
        <div className="card mb-4 text-sm" style={{ borderLeft: `4px solid ${message.ok ? "var(--accent-green)" : "var(--accent-red)"}`, color: message.ok ? "inherit" : "var(--accent-red)" }}>
          {message.text}
        </div>
      )}
      {loading && <p>加载中...</p>}
      {!loading && !report && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无日报数据</div>}
      {report && (
        <>
          <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
            日期：{report.calc_date}{report.data_date && report.data_date !== report.calc_date ? `（数据截至 ${report.data_date}）` : ""}
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 mb-6">
            {stats.map(([label, val, color]) => (
              <div key={label} className="stat-card" style={{ borderLeft: color ? `4px solid ${color}` : "" }}>
                <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>{label}</p>
                <p className="text-2xl font-bold" style={{ color: color || "inherit" }}>{val ?? "-"}</p>
              </div>
            ))}
          </div>

          {report.ai_summary && (
            <div className="card mb-6" style={{ borderLeft: "4px solid var(--accent-blue)" }}>
              <h2 className="text-lg font-semibold mb-2">🤖 AI 日报总结</h2>
              <p className="text-sm whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{report.ai_summary}</p>
            </div>
          )}

          <div className="card mb-6 overflow-x-auto">
            <h2 className="text-lg font-semibold mb-3">🔔 重点提醒（TOP{report.top_alerts.length}）</h2>
            {report.top_alerts.length === 0 && <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>今日暂无风险提醒，整体库存状况良好</p>}
            {report.top_alerts.length > 0 && (
              <table className="w-full text-sm">
                <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <th className="text-left py-2 pr-3">ASIN</th>
                  <th className="text-left py-2 pr-3">产品名称</th>
                  <th className="text-center py-2 pr-3">提醒</th>
                  <th className="text-center py-2 pr-3">级别</th>
                  <th className="text-center py-2 pr-3">主运营负责人</th>
                  <th className="text-right py-2 pr-3">评分</th>
                  <th className="text-right py-2 pr-3">原始分</th>
                  <th className="text-right py-2 pr-3">建议数量</th>
                  <th className="text-right py-2 pr-3">库存天数</th>
                  <th className="text-left py-2">分析</th>
                </tr></thead>
                <tbody>{report.top_alerts.map(a => {
                  const ls = LEVEL_STYLE[a.purchase_level || ""] || LEVEL_STYLE["未触发"];
                  const as = ALERT_STYLE[a.alert_type || ""] || null;
                  return (
                    <tr key={a.asin} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{a.asin}</td>
                      <td className="py-2 pr-3 truncate max-w-[180px]">{a.product_name || "-"}</td>
                      <td className="py-2 pr-3 text-center">
                        {as && <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: as.bg, color: as.color }}>{a.alert_type}</span>}
                      </td>
                      <td className="py-2 pr-3 text-center">
                        {a.purchase_level && <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: ls.bg, color: ls.color }}>{a.purchase_level}</span>}
                      </td>
                      <td className="py-2 pr-3">
                        <span className="flex flex-wrap gap-1">
                          {a.primary_operator ? (
                            <span className="px-1.5 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>{a.primary_operator}</span>
                          ) : "-"}
                        </span>
                      </td>
                      <td className="py-2 pr-3 text-right font-bold">{a.purchase_score ?? "-"}</td>
                      <td className="py-2 pr-3 text-right">{a.base_score ?? "-"}</td>
                      <td className="py-2 pr-3 text-right font-mono">{a.suggested_qty?.toLocaleString() ?? "-"}</td>
                      <td className="py-2 pr-3 text-right">{a.inventory_days ?? "-"}</td>
                      <td className="py-2 text-xs max-w-[300px]">
                        <span style={{ color: a.ai_analysis ? "var(--text-secondary)" : "var(--text-tertiary)" }}>
                          {a.alert_type === "断货风险"
                            ? (a.alert_reason || a.ai_analysis || a.purchase_trigger || "-")
                            : (a.ai_analysis || a.alert_reason || a.purchase_trigger || "-")}
                        </span>
                      </td>
                    </tr>
                  );
                })}</tbody>
              </table>
            )}
          </div>

          {report.results?.length > 0 && (
            <div className="card">
              <h2 className="text-lg font-semibold mb-3">所有 ASIN 结果（{report.results.length}）</h2>
              <div className="overflow-x-auto max-h-96 overflow-y-auto">
                <table className="w-full text-sm">
                  <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <th className="text-left py-2 pr-3">ASIN</th>
                    <th className="text-left py-2 pr-3">产品名称</th>
                    <th className="text-left py-2 pr-3">主运营负责人</th>
                    <th className="text-center py-2 pr-3">评分</th>
                    <th className="text-center py-2 pr-3">原始分</th>
                    <th className="text-center py-2 pr-3">级别</th>
                    <th className="text-right py-2 pr-3">库存天数</th>
                    <th className="text-right py-2 pr-3">可用库存</th>
                    <th className="text-right py-2 pr-3">补货周期</th>
                    <th className="text-right py-2">建议数量</th>
                  </tr></thead>
                  <tbody>{report.results.map((r, i) => (
                    <>
                      <tr key={r.asin || i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                        <td className="py-1.5 pr-3 font-mono text-xs whitespace-nowrap">{r.asin}</td>
                        <td className="py-1.5 pr-3 text-xs max-w-[200px] truncate">{r.product_name || "-"}</td>
                        <td className="py-1.5 pr-3 text-xs">
                          {r.primary_operator ? (
                            <span className="px-1.5 py-0.5 rounded text-[10px] font-medium mr-1" style={{ backgroundColor: "var(--bg-tertiary)" }}>{r.primary_operator}</span>
                          ) : "-"}
                        </td>
                        <td className="py-1.5 pr-3 text-center">{r.purchase_score ?? "-"}</td>
                        <td className="py-1.5 pr-3 text-center">{r.base_score ?? "-"}</td>
                        <td className="py-1.5 pr-3 text-center">
                          <span className="px-1.5 py-0.5 rounded text-xs" style={{
                            backgroundColor: r.purchase_level === "立即采购" ? "#fef3c7" : r.purchase_level === "观察" ? "#dbeafe" : "#f1f5f9",
                          }}>{r.purchase_level || "-"}</span></td>
                        <td className="py-1.5 pr-3 text-right">{r.inventory_days ?? "-"}</td>
                        <td className="py-1.5 pr-3 text-right font-mono">{r.available_stock?.toLocaleString() ?? "-"}</td>
                        <td className="py-1.5 pr-3 text-right">{r.replenishment_cycle ?? "-"}</td>
                        <td className="py-1.5 text-right font-mono">{r.suggested_qty?.toLocaleString() ?? "-"}</td>
                      </tr>
                      <tr key={`${r.asin || i}-detail`} style={{ borderBottom: "1px solid var(--border-color)", background: "var(--bg-secondary)" }}>
                        <td colSpan={10} className="px-4 py-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                          <div className="space-y-1">
                            {r.sales_trend && <div>📉 销量趋势：{r.sales_trend.text}</div>}
                            {(r.thirty_spend != null || r.acos_30d != null || r.profit_rate != null || r.est_profit_rate != null) && (
                              <div>
                                📣 广告/利润：
                                {r.thirty_spend != null && <>30天花费 ${r.thirty_spend.toFixed(2)}</>}
                                {r.ad_spend_ratio != null && <>（占销售额 {r.ad_spend_ratio}%）</>}
                                {r.acos_30d != null && <>｜ACOS {Math.round(r.acos_30d * 100)}%</>}
                                {r.profit_rate != null && <>｜利润率 {Math.round(r.profit_rate * 100)}%</>}
                                {r.profit_rate == null && r.est_profit_rate != null && <>｜估算毛利率 {Math.round(r.est_profit_rate * 100)}%</>}
                                {r.profit_rate == null && r.est_profit_rate == null && <>｜利润率待回填</>}
                              </div>
                            )}
                            {r.cost_table?.channels && (
                              <div>
                                📊 成本表（三渠道）：
                                {(["sea", "air", "express"] as const).map(mode => {
                                  const c = r.cost_table?.channels[mode];
                                  if (!c) return null;
                                  const m = c.margin != null ? Math.round(c.margin * 100) : 0;
                                  return `${c.label} $${c.profit.toFixed(2)}（${m}%）${c.profitable ? "" : " ✗"}`;
                                }).filter(Boolean).join(" ｜ ")}
                                {r.cost_price_cny != null && r.cost_price != null && <>｜采购成本 ￥{r.cost_price_cny}（${r.cost_price.toFixed(2)}/件）</>}
                                {r.cost_table?.is_peak ? "（旺季运费）" : "（淡季运费）"}
                              </div>
                            )}
                            <div>
                              ⚡ 触发：{r.purchase_trigger || "-"}
                              {r.forecast_total != null && <> ｜ 📈 未来预测：{r.forecast_total.toLocaleString()} 件（{(r.forecast_months || []).map(m => `${parseInt(m.month.slice(5), 10)}月${m.forecast_qty}`).join(" · ") || "-"}）</>}
                            </div>
                            {r.score_detail_text && <div>🧮 评分明细：{r.score_detail_text}</div>}
                            {r.batch_plan_text && <div>📦 批次规划：{r.batch_plan_text}</div>}
                            {r.ai_analysis && <div>🤖 AI 分析：{r.ai_analysis}</div>}
                          </div>
                        </td>
                      </tr>
                    </>
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
