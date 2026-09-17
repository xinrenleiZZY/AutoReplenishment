"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, type CostTable, type HistoryItem } from "@/lib/api";

interface StepInfo {
  step_no: number;
  step_name: string;
  status: string;
  input?: any;
  output?: any;
  reason?: string;
  computed_at?: string;
}

export default function CalculationDetailPage() {
  const params = useParams();
  const asin = params.asin as string;
  const [result, setResult] = useState<any>(null);
  const [steps, setSteps] = useState<StepInfo[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [aiResult, setAiResult] = useState<any>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [costTable, setCostTable] = useState<CostTable | null>(null);
  const [simPrice, setSimPrice] = useState<string>("");
  const [costLoading, setCostLoading] = useState(false);
  const [costError, setCostError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.calculation.latest(asin).catch(() => null),
      api.calculation.latestSteps(asin).catch(() => null),
      api.calculation.history(asin).catch(() => [] as HistoryItem[]),
      api.ai.latest(asin).catch(() => null),
      api.calculation.costTable(asin).catch(() => null),
    ]).then(([res, stepsData, hist, aiData, cost]) => {
      setResult(res);
      setSteps((stepsData?.steps as StepInfo[] | undefined) || []);
      setHistory(hist || []);
      if (aiData && aiData.status !== "none") setAiResult(aiData);
      setCostTable(cost);
      setLoading(false);
    });
  }, [asin]);

  if (loading) return <p>加载中...</p>;
  if (!result?.id) return <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无计算结果，<Link href={`/products/${asin}`} style={{ color: "var(--accent-blue)" }}>返回产品页</Link></div>;

  let scoreDetail: Record<string, any> = {};
  try { scoreDetail = JSON.parse(result.score_detail || "{}"); } catch {}

  const runAi = async () => {
    setAiLoading(true);
    setAiError(null);
    try {
      setAiResult(await api.ai.evaluate(asin));
    } catch (e) {
      setAiError(e instanceof Error ? e.message : "AI 分析失败");
    } finally {
      setAiLoading(false);
    }
  };

  const recalcCost = async () => {
    setCostLoading(true);
    setCostError(null);
    try {
      const price = simPrice.trim() === "" ? undefined : Number(simPrice);
      setCostTable(await api.calculation.costTable(asin, price));
    } catch (e) {
      setCostError(e instanceof Error ? e.message : "成本表计算失败");
    } finally {
      setCostLoading(false);
    }
  };

  return (
    <div>
      <Link href="/calculation" className="text-sm mb-4 inline-block" style={{ color: "var(--accent-blue)" }}>← 返回列表</Link>
      <h1 className="text-xl font-bold mb-1">
        <span className="font-mono">{asin}</span>
        {result.product_name && (
          <span className="ml-2" style={{ color: "var(--text-secondary)", fontWeight: 500 }}>{result.product_name}</span>
        )}
        {result.product_stage && (
          <span className="ml-2 px-2 py-0.5 rounded text-xs font-medium"
            style={{ backgroundColor: result.product_stage === "新品" ? "#dbeafe" : "#f1f5f9",
                     color: result.product_stage === "新品" ? "#1e40af" : "#64748b" }}>
            {result.product_stage}
          </span>
        )}
      </h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>计算日期：{result.calc_date}</p>

      <div className="card mb-6 text-center py-8" style={{ borderLeft: `6px solid ${(result.purchase_score ?? 0) >= 80 ? "#fef3c7" : "#f1f5f9"}` }}>
        <p className="text-sm mb-1" style={{ color: "var(--text-tertiary)" }}>采购评分</p>
        <p className="text-5xl font-bold mb-2" style={{ color: (result.purchase_score ?? 0) >= 80 ? "#92400e" : "#475569" }}>{result.purchase_score}</p>
        <span className="px-3 py-1 rounded text-sm font-medium"
          style={{ backgroundColor: result.purchase_level === "立即采购" ? "#fef3c7" : result.purchase_level === "观察" ? "#dbeafe" : "#f1f5f9" }}>
          {result.purchase_level}
        </span>
        {result.base_score != null && result.base_score !== result.purchase_score && (
          <p className="mt-2 text-sm" style={{ color: "var(--text-secondary)" }}>
            原始公式分：<b>{result.base_score}</b>
            <span className="ml-1 text-xs" style={{ color: "var(--text-tertiary)" }}>（特例加订/不加订调整前）</span>
          </p>
        )}
      </div>

      <div className="card mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">🤖 DeepSeek AI 评估</h2>
          <button onClick={runAi} disabled={aiLoading}
            className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: aiLoading ? "#94a3b8" : "var(--accent-blue)" }}>
            {aiLoading ? "评估中..." : aiResult ? "重新评估" : "AI 评估"}
          </button>
        </div>
        {aiError && <p className="text-sm" style={{ color: "var(--accent-red)" }}>{aiError}</p>}
            {aiResult && (
              <div className="text-sm space-y-2">
                {aiResult.factors && Object.keys(aiResult.factors).length > 0 && (
                  <div className="rounded-md p-3" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                    <p className="text-xs font-semibold mb-1" style={{ color: "var(--text-secondary)" }}>多维评估</p>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1">
                      {Object.entries(aiResult.factors).map(([k, v]) => (
                        <p key={k} className="text-xs" style={{ color: "var(--text-secondary)" }}>
                          <span className="font-medium" style={{ color: "var(--text-primary)" }}>{k}：</span>{String(v)}
                        </p>
                      ))}
                    </div>
                  </div>
                )}
                {aiResult.forecast && (
              <div className="rounded-md p-3" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                <p className="text-xs font-semibold mb-1" style={{ color: "var(--text-secondary)" }}>📈 未来销量评估</p>
                <p className="whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>
                  {aiResult.forecast.assessment || ""}
                  {aiResult.forecast.suggested_forecast_total != null && (
                    <span className="ml-1">建议预测总量：<b>{Number(aiResult.forecast.suggested_forecast_total).toLocaleString()}</b></span>
                  )}
                  {aiResult.forecast.trend && <span className="ml-1">趋势：{aiResult.forecast.trend}</span>}
                </p>
              </div>
            )}
            <p>
              <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>结论：</span>
              <span className="px-2 py-0.5 rounded text-xs font-medium"
                style={{ backgroundColor: aiResult.conclusion === "建议采购" ? "#fef3c7" : aiResult.conclusion === "观察" ? "#dbeafe" : "#f1f5f9" }}>
                {aiResult.conclusion ?? "-"}
              </span>
              {aiResult.suggested_qty != null && <span className="ml-2">建议数量：<b>{Number(aiResult.suggested_qty).toLocaleString()}</b></span>}
              {aiResult.confidence && <span className="ml-2">信心：{aiResult.confidence}</span>}
            </p>
            <p className="whitespace-pre-wrap" style={{ color: "var(--text-secondary)" }}>{aiResult.reason || aiResult.error || ""}</p>
            {aiResult.risks?.length > 0 && (
              <ul className="list-disc pl-5 text-xs" style={{ color: "var(--accent-red)" }}>
                {aiResult.risks.map((r: string, i: number) => <li key={i}>{r}</li>)}
              </ul>
            )}
            {aiResult.status === "fallback" && <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>AI 调用失败，已回退到规则原因。</p>}
          </div>
        )}
        {!aiResult && !aiError && (
          <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>基于销量趋势、库存、成本表利润、近30天历史，由 DeepSeek 生成综合采购建议。</p>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">评分明细</h2>
          {Object.entries(scoreDetail).length === 0 && <p style={{ color: "var(--text-tertiary)" }}>暂无评分明细</p>}
          {(() => {
            const isNewDecision = scoreDetail.triggered !== undefined || Array.isArray(scoreDetail.steps);
            const isDim = (d: any) => d && typeof d === "object" && !Array.isArray(d) && typeof d.value === "number";
            // 六维评分可能嵌套在 scoreDetail["六维评分"]（新品门禁），也可能平铺在顶层（老品），统一提取
            const dimsSource: Record<string, any> =
              scoreDetail["六维评分"] && typeof scoreDetail["六维评分"] === "object"
                ? scoreDetail["六维评分"]
                : scoreDetail;
            const dimEntries = Object.entries(dimsSource).filter(([, d]) => isDim(d));
            const specialEntries = Object.entries(scoreDetail).filter(([k, d]) => !isDim(d) && k !== "六维评分");
            const LEVEL3: Record<string, string> = { "立即采购": "立即采购", "建议采购": "立即采购", "观察": "观察", "暂停": "暂停", "未触发": "暂停", "终止": "暂停" };
            const fmtSpecial = (k: string, d: any) => {
              if ((k === "特例加订" || k === "特例不加订") && Array.isArray(d)) {
                return (
                  <ul className="list-disc pl-4 space-y-0.5">
                    {d.map((c: any, i: number) => (
                      <li key={i}><b>{c.name || `情况${c.id}`}</b>：{c.detail}</li>
                    ))}
                  </ul>
                );
              }
              if (k === "特例加订建议量" && d && typeof d === "object") {
                return <span>建议量 <b>{d.qty}</b>（AI原始建议 {d.ai_qty ?? "-"}）：{d.detail || ""}</span>;
              }
              if (k === "AI否决加订" && d && typeof d === "object") {
                return <span>AI 结论 <b>{d.conclusion}</b>：{d.reason || ""}</span>;
              }
              return typeof d === "string" ? d : JSON.stringify(d);
            };
            if (isNewDecision) {
              // 新品门禁决策：等级/建议量/原因/门禁步骤/三渠道盈利/特例判定
              const steps = Array.isArray(scoreDetail.steps) ? scoreDetail.steps : [];
              const ct = scoreDetail.cost_table || {};
              return (
                <div className="space-y-3 text-sm">
                  {dimEntries.length > 0 && (
                    <div className="space-y-4">
                      <SixDimRadar dims={dimsSource} />
                      {dimEntries.map(([key, detail]: [string, any]) => {
                        const raw = detail.value || 0;
                        const weightPct = Math.round((detail.weight || 0) * 100);
                        return (
                          <div key={key}>
                            <div className="flex justify-between text-sm mb-1">
                              <span>{detail.label || key}</span>
                              <span className="font-medium">{raw}分</span>
                            </div>
                            <div className="w-full h-2 rounded-full" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                              <div className="h-2 rounded-full transition-all"
                                style={{ width: `${Math.min(raw, 100)}%`, backgroundColor: raw >= 80 ? "var(--accent-green)" : raw >= 60 ? "var(--accent-orange)" : "var(--accent-red)" }} />
                            </div>
                            <p className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>权重 {weightPct}%</p>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {scoreDetail.level && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>决策等级</span>
                      <span className="px-2 py-0.5 rounded text-xs font-medium"
                        style={{ backgroundColor: LEVEL3[scoreDetail.level] === "立即采购" ? "#fef3c7" : LEVEL3[scoreDetail.level] === "观察" ? "#dbeafe" : "#f1f5f9" }}>
                        {LEVEL3[scoreDetail.level] || scoreDetail.level}
                      </span>
                      {scoreDetail["原始等级"] && scoreDetail["原始等级"] !== scoreDetail.level && (
                        <span className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
                          （新品门禁原判：{scoreDetail["原始等级"]}，被特例判定覆盖）
                        </span>
                      )}
                    </div>
                  )}
                  {scoreDetail.suggested_qty != null && (
                    <p>建议数量：<b>{Number(scoreDetail.suggested_qty).toLocaleString()}</b> 件</p>
                  )}
                  {scoreDetail.reason && <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{scoreDetail.reason}</p>}
                  {steps.length > 0 && (
                    <div>
                      <p className="text-xs font-semibold mb-1" style={{ color: "var(--text-secondary)" }}>新品门禁流程</p>
                      <ul className="space-y-1">
                        {steps.map((s: any, i: number) => (
                          <li key={i} className="text-xs flex items-start gap-2">
                            <span className="px-1.5 py-0.5 rounded text-[10px] font-bold shrink-0"
                              style={{ backgroundColor: s.status === "pass" ? "#dcfce7" : "#fee2e2", color: s.status === "pass" ? "#166534" : "#dc2626" }}>
                              {s.status === "pass" ? "通过" : "未过"}
                            </span>
                            <span style={{ color: "var(--text-primary)" }}>{s.name}</span>
                            <span className="flex-1" style={{ color: "var(--text-tertiary)" }}>{s.reason}</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {ct.channels && (
                    <div className="grid grid-cols-3 gap-2">
                      {(["sea", "air", "express"] as const).map(m => {
                        const c = ct.channels[m];
                        if (!c) return null;
                        return (
                          <div key={m} className="rounded-md p-2 text-center"
                            style={{ backgroundColor: c.profitable ? "rgba(34,197,94,.1)" : "rgba(239,68,68,.08)" }}>
                            <p className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>{c.label}</p>
                            <p className="text-sm font-bold" style={{ color: c.profitable ? "#166534" : "#dc2626" }}>${c.profit?.toFixed(2)}</p>
                            <p className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{c.margin != null ? `${(c.margin * 100).toFixed(1)}%` : "-"}</p>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {Object.entries(scoreDetail).filter(([k]) => k === "特例加订" || k === "特例不加订").map(([k, d]) => (
                    <div key={k} className="text-xs" style={{ color: "var(--text-secondary)" }}>
                      <b>{k}：</b>{fmtSpecial(k, d)}
                    </div>
                  ))}
                </div>
              );
            }
            // 老品六维评分
            return (
              <div>
                {dimEntries.length > 0 && <SixDimRadar dims={dimsSource} />}
                <div className="space-y-4">
                  {dimEntries.map(([key, detail]: [string, any]) => {
                    const raw = detail.value || 0;
                    const weightPct = Math.round((detail.weight || 0) * 100);
                    return (
                      <div key={key}>
                        <div className="flex justify-between text-sm mb-1">
                          <span>{detail.label || key}</span>
                          <span className="font-medium">{raw}分</span>
                        </div>
                        <div className="w-full h-2 rounded-full" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                          <div className="h-2 rounded-full transition-all"
                            style={{ width: `${Math.min(raw, 100)}%`, backgroundColor: raw >= 80 ? "var(--accent-green)" : raw >= 60 ? "var(--accent-orange)" : "var(--accent-red)" }} />
                        </div>
                        <p className="text-[10px] mt-0.5" style={{ color: "var(--text-tertiary)" }}>权重 {weightPct}%</p>
                      </div>
                    );
                  })}
                </div>
                {specialEntries.length > 0 && (
                  <div className="mt-4 pt-3 space-y-1.5" style={{ borderTop: "1px solid var(--border-color)" }}>
                    {specialEntries.map(([k, d]) => (
                      <div key={k} className="text-xs" style={{ color: "var(--text-secondary)" }}>
                        <b>{k}：</b>{fmtSpecial(k, d)}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
        </div>

        <div className="card">
          <h2 className="text-lg font-semibold mb-4">决策数据</h2>
          {[
            ["采购触发", result.purchase_trigger],
            ["建议采购数量", result.suggested_qty?.toLocaleString()],
            ["库存覆盖天数", result.inventory_days != null ? result.inventory_days + " 天" : "-"],
            ["补货周期", result.replenishment_cycle != null ? result.replenishment_cycle + " 天" : "-"],
            ["预测总销量", result.forecast_total?.toLocaleString()],
            ["紧急程度评分", result.urgency_score != null ? result.urgency_score : "-"],
          ].map(([label, value]) => (
            <div key={label} className="flex justify-between py-2 text-sm" style={{ borderBottom: "1px solid var(--border-color)" }}>
              <span style={{ color: "var(--text-tertiary)" }}>{label}</span>
              <span className="font-bold">{value ?? "-"}</span>
            </div>
          ))}
        </div>
      </div>

      {/* 成本表（三渠道 Profit） */}
      <div className="card mt-6">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <h2 className="text-lg font-semibold">📊 成本表（三渠道 Profit）</h2>
          <div className="flex items-center gap-2 text-sm">
            <input
              type="number"
              step="0.01"
              min="0"
              placeholder={costTable?.price != null ? `竞对售价（当前 $${costTable.price}）` : "竞对旺季售价 $"}
              value={simPrice}
              onChange={e => setSimPrice(e.target.value)}
              className="px-2 py-1 rounded border"
              style={{ width: 170, borderColor: "var(--border-color)", background: "var(--bg-secondary)" }}
            />
            <button onClick={recalcCost} disabled={costLoading}
              className="px-3 py-1 rounded-md text-xs font-medium text-white"
              style={{ backgroundColor: costLoading ? "#94a3b8" : "var(--accent-blue)" }}>
              {costLoading ? "重算中..." : "模拟售价重算"}
            </button>
          </div>
        </div>
        <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>
          售价 − 采购单价 − 运费(折算USD) − FBA费 − 佣金 = 单件利润；填竞对旺季售价可推断我们旺季可达到的售价。
          {costTable?.is_peak ? "（当前为旺季运费：海运￥15/空派￥60/快递￥70）" : "（当前为淡季运费：海运￥17/空派￥70/快递￥80）"}
          {costTable?.freight_basis === "weight" ? ` ｜ 按重量计费（${costTable.weight_kg ?? 0} kg）` : " ｜ 缺重量，按件计费"}
        </p>
        {costError && <p className="text-xs mb-2" style={{ color: "var(--accent-red)" }}>{costError}</p>}
        {!costTable && !costError && <p className="text-sm" style={{ color: "var(--text-tertiary)" }}>加载中...</p>}
        {costTable && (
          <>
            <table className="w-full text-sm mb-3">
              <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th className="text-left py-2 pr-3">渠道</th>
                <th className="text-right py-2 pr-3">售价($)</th>
                <th className="text-right py-2 pr-3">运费(￥)</th>
                <th className="text-right py-2 pr-3">运费($)</th>
                <th className="text-right py-2 pr-3">单件利润($)</th>
                <th className="text-right py-2 pr-3">毛利率</th>
                <th className="text-center py-2">是否盈利</th>
              </tr></thead>
              <tbody>
                {(["sea", "air", "express"] as const).map(mode => {
                  const c = costTable.channels[mode];
                  if (!c) return null;
                  return (
                    <tr key={mode} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td className="py-2 pr-3">{c.label}</td>
                      <td className="py-2 pr-3 text-right">${costTable.price.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right">￥{c.freight_fee}</td>
                      <td className="py-2 pr-3 text-right">${c.freight_fee_usd.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right font-mono" style={{ color: c.profit >= 0 ? "var(--accent-green)" : "var(--accent-red)" }}>
                        ${c.profit.toFixed(2)}
                      </td>
                      <td className="py-2 pr-3 text-right">{c.margin != null ? `${(c.margin * 100).toFixed(1)}%` : "-"}</td>
                      <td className="py-2 text-center">
                        <span className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{ backgroundColor: c.profitable ? "#dcfce7" : "#fee2e2", color: c.profitable ? "#166534" : "#dc2626" }}>
                          {c.profitable ? "盈利" : "亏损"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <p className="text-sm" style={{ borderLeft: `4px solid ${costTable.conclusion_ok ? "var(--accent-green)" : "var(--accent-red)"}`, paddingLeft: 10 }}>
              {costTable.conclusion}
            </p>
            {costTable.cost_price_cny != null && (
              <p className="text-xs mt-2" style={{ color: "var(--text-tertiary)" }}>
                采购成本（ERP cg_price，人民币）：￥{costTable.cost_price_cny} / 件（≈ ${costTable.unit_cost.toFixed(4)} USD，按汇率 {costTable.exchange_rate ?? 7.2}）
              </p>
            )}
            {costTable.channels.sea && (
              <p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>
                海运成本明细：MC ${costTable.channels.sea.mc?.toFixed(2)}（采购+运费）｜P卡 ${costTable.channels.sea.packing_card?.toFixed(2)}｜分拣 ${costTable.channels.sea.sorting_fee?.toFixed(2)}｜佣金 ${costTable.channels.sea.referral_fee?.toFixed(2)}｜入库 ${costTable.channels.sea.inbound_fee?.toFixed(2)}｜仓储 ${costTable.channels.sea.storage_fee?.toFixed(2)}｜广告 ${costTable.channels.sea.ad_fee?.toFixed(2)}｜退货 ${costTable.channels.sea.return_loss?.toFixed(2)}｜附加 ${costTable.channels.sea.misc_fee?.toFixed(2)}
              </p>
            )}
          </>
        )}
      </div>

      {/* 历史对比趋势 */}
      <div className="card mt-6">
        <h2 className="text-lg font-semibold mb-1">历史对比趋势</h2>
        <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
          同一 ASIN 不同计算日期的采购评分与建议数量变化（共 {history.length} 次）
        </p>
        {history.length < 2 && (
          <p style={{ color: "var(--text-tertiary)" }}>暂无足够历史数据，连续多日计算后将在此形成趋势。</p>
        )}
        {history.length >= 2 && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <TrendChart
              title="采购评分"
              items={history}
              valueOf={h => h.purchase_score ?? 0}
              max={100}
              color="var(--accent-blue)"
            />
            <TrendChart
              title="建议采购数量"
              items={history}
              valueOf={h => h.suggested_qty ?? 0}
              max={Math.max(...history.map(h => h.suggested_qty ?? 0), 1)}
              color="var(--accent-green)"
            />
          </div>
        )}
      </div>

      {/* 分析过程溯源 */}
      <div className="card mt-6">
        <h2 className="text-lg font-semibold mb-1">分析过程溯源</h2>
        <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
          每一步的输入数据、分析结果和判断依据（共 {steps.length} 步）
        </p>
        {steps.length === 0 && <p style={{ color: "var(--text-tertiary)" }}>暂无步骤记录（需重新触发计算后生成）</p>}
        <div className="space-y-2">
          {steps.map(step => (
            <div key={step.step_no} className="rounded-lg" style={{ border: "1px solid var(--border-color)" }}>
              <button
                className="w-full flex items-center gap-3 px-4 py-3 text-left"
                onClick={() => setExpandedStep(expandedStep === step.step_no ? null : step.step_no)}
              >
                <span className="w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold"
                  title={step.step_no >= 100 ? "新品决策步骤" : undefined}
                  style={{ backgroundColor: step.status === "error" ? "#fee2e2" : "var(--bg-tertiary)", color: step.status === "error" ? "#dc2626" : "var(--text-primary)" }}>
                  {step.step_no >= 100 ? `N${step.step_no - 100}` : step.step_no}
                </span>
                <span className="flex-1 font-medium text-sm">{step.step_name}</span>
                {step.reason && <span className="text-xs truncate max-w-[40%]" style={{ color: "var(--text-tertiary)" }}>{step.reason}</span>}
                <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>{expandedStep === step.step_no ? "▲" : "▼"}</span>
              </button>
              {expandedStep === step.step_no && (
                <div className="px-4 pb-4 pt-1 text-sm space-y-3" style={{ borderTop: "1px solid var(--border-color)" }}>
                  {step.reason && (
                    <div>
                      <p className="text-xs mb-1 font-medium" style={{ color: "var(--text-tertiary)" }}>判断依据</p>
                      <p className="whitespace-pre-wrap">{step.reason}</p>
                    </div>
                  )}
                  {step.output && (
                    <div>
                      <p className="text-xs mb-1 font-medium" style={{ color: "var(--text-tertiary)" }}>分析结果</p>
                      <pre className="rounded p-2 overflow-x-auto text-xs" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                        {JSON.stringify(step.output, null, 2)}
                      </pre>
                    </div>
                  )}
                  {step.input && (
                    <div>
                      <p className="text-xs mb-1 font-medium" style={{ color: "var(--text-tertiary)" }}>输入数据</p>
                      <pre className="rounded p-2 overflow-x-auto text-xs" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                        {JSON.stringify(step.input, null, 2)}
                      </pre>
                    </div>
                  )}
                  {step.computed_at && (
                    <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>计算时间：{step.computed_at}</p>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const TREND_PLOT_H = 128;
const TREND_COL_W = 40;

function TrendChart({ title, items, valueOf, max, color }: {
  title: string;
  items: HistoryItem[];
  valueOf: (h: HistoryItem) => number;
  max: number;
  color: string;
}) {
  const safeMax = max > 0 ? max : 1;
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3">{title}</h3>
      <div className="overflow-x-auto pb-1">
        <div className="min-w-max">
          <div className="flex items-end gap-1">
            {items.map(h => {
              const v = valueOf(h);
              const hgt = v > 0 ? Math.min(Math.max((v / safeMax) * TREND_PLOT_H, 4), TREND_PLOT_H) : 2;
              return (
                <div key={h.calc_date} className="shrink-0 flex flex-col items-center" style={{ width: TREND_COL_W }}
                  title={`${h.calc_date}：${v}`}>
                  <span className="w-full h-4 leading-4 text-center text-[10px] truncate"
                    style={{ color: "var(--text-secondary)" }}>
                    {v > 0 ? v : ""}
                  </span>
                  <div className="w-5 rounded-t" style={{ height: `${hgt}px`, backgroundColor: color, opacity: v > 0 ? 0.9 : 0.3 }} />
                </div>
              );
            })}
          </div>
          <div className="flex items-start gap-1 border-t pt-1" style={{ borderColor: "var(--border-color)" }}>
            {items.map(h => (
              <span key={h.calc_date} className="shrink-0 text-center text-[10px] whitespace-nowrap"
                style={{ width: TREND_COL_W, color: "var(--text-tertiary)" }}>
                {h.calc_date.slice(5)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 六维评分雷达图（纯 SVG，无第三方依赖） */
function SixDimRadar({ dims }: { dims: Record<string, any> }) {
  const entries = Object.entries(dims || {}).filter(
    ([, d]) => d && typeof d === "object" && !Array.isArray(d) && typeof d.value === "number"
  );
  if (entries.length === 0) return null;

  const cx = 150, cy = 130, R = 92;
  const pt = (i: number, ratio: number): [number, number] => {
    const ang = ((-90 + i * (360 / entries.length)) * Math.PI) / 180;
    return [cx + R * ratio * Math.cos(ang), cy + R * ratio * Math.sin(ang)];
  };
  const grid = [0.25, 0.5, 0.75, 1]
    .map(r => entries.map((_, i) => pt(i, r).join(",")).join(" "));
  const valuePoly = entries
    .map(([, d], i) => pt(i, Math.min(Math.max((d.value || 0) / 100, 0), 1)).join(","))
    .join(" ");

  return (
    <svg viewBox="0 0 300 265" className="w-full max-w-sm mx-auto" role="img" aria-label="六维评分雷达图">
      {grid.map((points, i) => (
        <polygon key={i} points={points} fill="none" stroke="var(--border-color)" strokeWidth={1} />
      ))}
      {entries.map(([k, d], i) => {
        const [x, y] = pt(i, 1.12);
        const [vx, vy] = pt(i, 1);
        const [lx, ly] = pt(i, 0.78);
        const value = Math.round(d.value || 0);
        return (
          <g key={k}>
            <line x1={cx} y1={cy} x2={vx} y2={vy} stroke="var(--border-color)" strokeWidth={1} />
            <text x={x} y={y} textAnchor="middle" dominantBaseline="middle" fontSize="10.5" fill="var(--text-secondary)">
              {d.label || k}
            </text>
            <text x={lx} y={ly} textAnchor="middle" dominantBaseline="middle" fontSize="11" fontWeight="bold" fill="var(--text-primary)">
              {value}
            </text>
          </g>
        );
      })}
      <polygon points={valuePoly} fill="rgba(59,130,246,.22)" stroke="#3b82f6" strokeWidth={2} strokeLinejoin="round" />
    </svg>
  );
}
