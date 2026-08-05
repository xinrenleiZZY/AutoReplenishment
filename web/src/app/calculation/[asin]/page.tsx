"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, type HistoryItem } from "@/lib/api";

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

  useEffect(() => {
    Promise.all([
      api.calculation.latest(asin).catch(() => null),
      api.calculation.latestSteps(asin).catch(() => null),
      api.calculation.history(asin).catch(() => [] as HistoryItem[]),
    ]).then(([res, stepsData, hist]) => {
      setResult(res);
      setSteps((stepsData?.steps as StepInfo[] | undefined) || []);
      setHistory(hist || []);
      setLoading(false);
    });
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
                  style={{ backgroundColor: step.status === "error" ? "#fee2e2" : "var(--bg-tertiary)", color: step.status === "error" ? "#dc2626" : "var(--text-primary)" }}>
                  {step.step_no}
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

function TrendChart({ title, items, valueOf, max, color }: {
  title: string;
  items: HistoryItem[];
  valueOf: (h: HistoryItem) => number;
  max: number;
  color: string;
}) {
  return (
    <div>
      <h3 className="text-sm font-semibold mb-3">{title}</h3>
      <div className="flex items-end gap-2 h-40">
        {items.map(h => {
          const v = valueOf(h);
          const hgt = max > 0 ? Math.max((v / max) * 100, 2) : 2;
          return (
            <div key={h.calc_date} className="flex-1 flex flex-col items-center gap-1" title={`${h.calc_date}：${v}`}>
              <span className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>{v}</span>
              <div className="w-full rounded-t" style={{ height: `${hgt}px`, backgroundColor: color, opacity: 0.85 }} />
              <span className="text-[10px]" style={{ color: "var(--text-tertiary)" }}>{h.calc_date.slice(5)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
