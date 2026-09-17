"use client";

import { useEffect, useState, useId } from "react";

interface Kpi { label: string; value: number | string; color: string; hint?: string }
interface RiskItem { asin: string; product_level?: string; reason: string; inventory_days?: number }
interface AlertItem { asin: string; product_name?: string; alert_type?: string; alert_reason?: string; purchase_level?: string; purchase_score?: number; suggested_qty?: number; inventory_days?: number }

const PALETTE = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#06b6d4", "#ec4899", "#84cc16", "#f97316", "#6366f1"];

// 平滑取整的 Y 轴最大刻度（1/2/2.5/5 ×10^n）
function niceMax(max: number) {
  if (max <= 0) return 1;
  const exp = Math.floor(Math.log10(max));
  const base = Math.pow(10, exp);
  const f = max / base;
  let nf = 10;
  if (f <= 1) nf = 1;
  else if (f <= 2) nf = 2;
  else if (f <= 2.5) nf = 2.5;
  else if (f <= 5) nf = 5;
  return nf * base;
}

// 柱线组合图：柱状图 + 趋势折线 + 完整 XY 轴
function BarChart({ data, color = "#3b82f6", height = 190 }: { data: { label: string; value: number }[]; color?: string; height?: number }) {
  const gid = useId();
  const total = data.reduce((s, d) => s + (Number(d.value) || 0), 0) || 1;
  const yMax = niceMax(Math.max(...data.map(d => Number(d.value) || 0), 1));
  const n = Math.max(data.length, 1);
  const w = 780, padL = 52, padR = 22, padT = 40, padB = 46;
  const plotW = w - padL - padR;
  const plotH = height;
  const bw = plotW / n;
  const barW = Math.min(bw * 0.5, 50);
  const base = padT + plotH;
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const fmt = (v: number) => {
    if (v >= 1000) { const k = v / 1000; return `${Number.isInteger(k) ? k : k.toFixed(1)}k`; }
    return `${Math.round(v)}`;
  };
  const pts = data.map((d, i) => {
    const v = Number(d.value) || 0;
    const cx = padL + i * bw + bw * 0.5;
    const y = base - (yMax ? (v / yMax) * plotH : 0);
    return { cx, y, v, pct: Math.round((v / total) * 100) };
  });
  const poly = pts.map(p => `${p.cx},${p.y}`).join(" ");
  const [h0, h1] = [color, color + "88"];
  const nz = pts.filter(p => p.v > 0).length;
  return (
    <svg viewBox={`0 0 ${w} ${height + padT + padB}`} width="100%" height={height + padT + padB}>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={h0} />
          <stop offset="100%" stopColor={h1} />
        </linearGradient>
      </defs>
      {/* 水平网格线 + Y 轴刻度 */}
      {ticks.map(t => {
        const y = base - t * plotH;
        return (
          <g key={t}>
            <line x1={padL} x2={w - padR} y1={y} y2={y} stroke={t === 0 ? "#cbd5e1" : "#eef2f7"} strokeWidth="1" strokeDasharray={t === 0 ? "0" : "3,4"} />
            <text x={padL - 8} y={y + 4} textAnchor="end" fontSize="10" fill="#94a3b8">{fmt(yMax * t)}</text>
          </g>
        );
      })}
      {/* Y 轴 / X 轴轴体 */}
      <line x1={padL} x2={padL} y1={padT} y2={base} stroke="#cbd5e1" strokeWidth="1" />
      <line x1={padL} x2={w - padR} y1={base} y2={base} stroke="#cbd5e1" strokeWidth="1" />
      {/* 柱体 */}
      {pts.map((p, i) => (
        p.v > 0 ? <rect key={`b${i}`} x={p.cx - barW / 2} y={p.y} width={barW} height={base - p.y} fill={`url(#${gid})`} rx={4} /> : null
      ))}
      {/* 趋势折线 + 数据点 */}
      {nz > 1 && (
        <>
          <polyline points={poly} fill="none" stroke="#0f172a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" opacity="0.7" />
          {pts.map((p, i) => (
            p.v > 0 ? (
              <g key={`p${i}`}>
                <circle cx={p.cx} cy={p.y} r="3.5" fill="#0f172a" />
                <text x={p.cx} y={p.y - 9} textAnchor="middle" fontSize="12" fontWeight="700" fill="#334155">{p.v.toLocaleString()}</text>
              </g>
            ) : null
          ))}
        </>
      )}
      {/* X 轴类别 + 占比 */}
      {pts.map((p, i) => (
        <g key={`x${i}`}>
          <text x={p.cx} y={base + 17} textAnchor="middle" fontSize="11" fill="#64748b">{data[i].label}</text>
          {p.v > 0 && <text x={p.cx} y={base + 33} textAnchor="middle" fontSize="10" fill="#94a3b8">{p.pct}%</text>}
        </g>
      ))}
    </svg>
  );
}

// 环形图：中心显示总数与最大占比
function DonutChart({ data, size = 190 }: { data: { label: string; value: number }[]; size?: number }) {
  const filtered = data.filter(d => (Number(d.value) || 0) > 0);
  const total = filtered.reduce((s, d) => s + (Number(d.value) || 0), 0);
  const cx = size / 2, cy = size / 2;
  const r = size / 2 - 18;
  const stroke = 22;
  const C = 2 * Math.PI * r;
  let acc = 0;
  const segs = filtered.map((d, i) => {
    const v = Number(d.value) || 0;
    const frac = total ? v / total : 0;
    const dash = frac * C;
    const offset = -acc * C;
    acc += frac;
    return { key: d.label, value: v, color: PALETTE[i % PALETTE.length], frac, dash, offset };
  });
  const top = filtered.length ? filtered.reduce((a, b) => (Number(a.value) || 0) > (Number(b.value) || 0) ? a : b) : null;
  const topPct = total && top ? Math.round((Number(top.value) / total) * 100) : 0;
  return (
    <div className="flex items-center gap-5 h-full">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} className="shrink-0">
        {segs.length > 0 ? (
          <>
            <circle cx={cx} cy={cy} r={r} fill="none" stroke="#f1f5f9" strokeWidth={stroke} />
            {segs.map((s, i) => (
              <circle key={i} cx={cx} cy={cy} r={r} fill="none" stroke={s.color} strokeWidth={stroke}
                strokeDasharray={`${Math.max(s.dash - 2, 0)} ${C - Math.max(s.dash - 2, 0)}`}
                strokeDashoffset={s.offset} transform={`rotate(-90 ${cx} ${cy})`} />
            ))}
            <text x={cx} y={cy + 1} textAnchor="middle" fontSize="20" fontWeight="700" fill="#0f172a">{total.toLocaleString()}</text>
            <text x={cx} y={cy + 17} textAnchor="middle" fontSize="9" fill="#94a3b8">总数</text>
          </>
        ) : (
          <text x={cx} y={cy} textAnchor="middle" fontSize="13" fill="#94a3b8">暂无数据</text>
        )}
      </svg>
      <div className="text-xs space-y-1.5 flex-1 min-w-0">
        {filtered.map((d, i) => {
          const v = Number(d.value) || 0;
          const pct = Math.round((v / total) * 100);
          return (
            <div key={d.label} className="flex items-center gap-2">
              <span style={{ width: 9, height: 9, borderRadius: 3, backgroundColor: PALETTE[i % PALETTE.length], display: "inline-block" }} />
              <span className="truncate" style={{ color: "var(--text-secondary)" }}>{d.label}</span>
              <b className="ml-auto">{v.toLocaleString()}</b>
              <span className="w-9 text-right" style={{ color: "var(--text-tertiary)" }}>{pct}%</span>
            </div>
          );
        })}
        {top && (
          <div className="mt-2 pt-2" style={{ borderTop: "1px dashed var(--border-color)" }}>
            <div className="flex items-center gap-2">
              <span style={{ width: 9, height: 9, borderRadius: 3, backgroundColor: PALETTE[filtered.findIndex(x => x.label === top.label) % PALETTE.length], display: "inline-block" }} />
              <span style={{ color: "var(--text-secondary)" }}>最大占比</span>
              <b className="ml-auto">{top.label}</b>
              <span className="w-9 text-right" style={{ color: "var(--text-tertiary)" }}>{topPct}%</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function BoardPage() {
  const [kpis, setKpis] = useState<Kpi[]>([]);
  const [scoreBuckets, setScoreBuckets] = useState<{ label: string; value: number }[]>([]);
  const [levelDist, setLevelDist] = useState<{ label: string; value: number }[]>([]);
  const [stockout, setStockout] = useState<RiskItem[]>([]);
  const [overstock, setOverstock] = useState<RiskItem[]>([]);
  const [profitRisks, setProfitRisks] = useState<RiskItem[]>([]);
  const [lifecycle, setLifecycle] = useState<{ label: string; value: number }[]>([]);
  const [levels, setLevels] = useState<{ label: string; value: number }[]>([]);
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [now, setNow] = useState<Date | null>(null);
  const [updatedAt, setUpdatedAt] = useState<string>("");

  const load = () => {
    Promise.all([
      fetch("/api/v1/calculation/daily-report?include_results=false").then(r => r.json()).catch(() => null),
      fetch("/api/v1/calculation/risks").then(r => r.json()).catch(() => null),
      fetch("/api/v1/products/stats/lifecycle").then(r => r.json()).catch(() => null),
      fetch("/api/v1/calculation/results/overview?limit=1000").then(r => r.json()).catch(() => null),
    ]).then(([rep, risks, life, ov]) => {
      if (rep) {
        setKpis([
          { label: "总 ASIN", value: rep.total_asins ?? "-", color: "#3b82f6", hint: "商品总数" },
          { label: "立即采购", value: rep.immediate_count ?? 0, color: "#ef4444", hint: "需尽快下单" },
          { label: "观察", value: rep.observe_count ?? 0, color: "#f59e0b", hint: "持续关注" },
          { label: "暂停", value: rep.pause_count ?? 0, color: "#10b981", hint: "暂缓采购" },
          { label: "断货风险", value: rep.stockout_count ?? 0, color: "#f97316", hint: "库存告急" },
          { label: "库存积压", value: rep.overstock_count ?? 0, color: "#8b5cf6", hint: "超过警戒" },
        ]);
        setAlerts(rep.top_alerts ?? []);
        setLevelDist([
          { label: "立即采购", value: rep.immediate_count ?? 0 },
          { label: "观察", value: rep.observe_count ?? 0 },
          { label: "暂停", value: rep.pause_count ?? 0 },
          { label: "未触发", value: rep.not_triggered_count ?? 0 },
          { label: "终止", value: rep.terminate_count ?? 0 },
        ]);
      }
      if (risks) {
        setStockout(risks.stockout ?? []);
        setOverstock(risks.overstock ?? []);
        setProfitRisks(risks.profit ?? []);
      }
      if (life) {
        setLifecycle((life.by_life_cycle ?? []).map((x: any) => ({ label: x.label, value: x.count ?? x.value ?? 0 })));
        setLevels((life.by_product_level ?? []).map((x: any) => ({ label: x.label, value: x.count ?? x.value ?? 0 })));
      }
      if (ov?.items) {
        const buckets = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        (ov.items as any[]).forEach((it: any) => {
          const s = it.purchase_score;
          if (typeof s === "number") {
            const idx = Math.min(Math.floor(s / 10), 9);
            buckets[idx] += 1;
          }
        });
        setScoreBuckets(buckets.map((v, i) => ({ label: `${i * 10}-${i * 10 + 9}`, value: v })));
      }
      setUpdatedAt(new Date().toLocaleTimeString("zh-CN", { hour12: false }));
    });
  };

  useEffect(() => {
    setNow(new Date());
    const t = setInterval(() => setNow(new Date()), 1000);
    load();
    const iv = setInterval(load, 60000);
    return () => { clearInterval(t); clearInterval(iv); };
  }, []);

  const timeText = now ? `${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}:${String(now.getSeconds()).padStart(2, "0")}` : "--:--:--";

  const Card = ({ title, icon, color = "#3b82f6", children }: { title: string; icon?: string; color?: string; children: React.ReactNode }) => (
    <div className="card flex flex-col" style={{ height: "100%", overflow: "hidden" }}>
      <div className="flex items-center gap-2 mb-3">
        <span style={{ width: 4, height: 16, borderRadius: 2, background: `linear-gradient(180deg, ${color}, ${color}66)`, display: "inline-block" }} />
        {icon && <span className="text-sm">{icon}</span>}
        <h3 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>{title}</h3>
      </div>
      <div className="flex-1">{children}</div>
    </div>
  );

  const RiskList = ({ items, empty = "暂无风险", warn = false }: { items: RiskItem[]; empty?: string; warn?: boolean }) => (
    <ul className="space-y-2 text-xs">
      {items.length === 0 && <li style={{ color: "var(--text-tertiary)" }}>{empty}</li>}
      {items.slice(0, 8).map(it => (
        <li key={it.asin} className="flex items-center gap-2">
          <span className="px-1.5 py-0.5 rounded font-mono font-semibold text-[10px]"
            style={{ backgroundColor: warn ? "#fef2f2" : "var(--bg-tertiary)", color: warn ? "#dc2626" : "#334155" }}>{it.asin}</span>
          {it.product_level && <span className="px-1 rounded text-[10px] font-bold" style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>{it.product_level}</span>}
          <span className="flex-1 truncate text-right" style={{ color: "var(--text-secondary)" }}>{it.reason}</span>
        </li>
      ))}
    </ul>
  );

  return (
    <div style={{ minHeight: "100vh", padding: "0 0 40px" }}>
      {/* 顶部 */}
      <div className="flex items-center justify-between mb-5">
        <h1 className="text-2xl font-bold" style={{ background: "linear-gradient(90deg,#3b82f6,#10b981)", WebkitBackgroundClip: "text", WebkitTextFillColor: "transparent" }}>
          自动补货决策 · 数据大屏
        </h1>
        <div className="flex items-center gap-4 text-sm" style={{ color: "var(--text-tertiary)" }}>
          <span className="font-mono tabular-nums">{timeText}</span>
          <span className="px-2 py-1 rounded-full text-xs" style={{ backgroundColor: "var(--bg-tertiary)" }}>更新 {updatedAt || "…"}</span>
        </div>
      </div>

      {/* KPI 行 */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-4 mb-6">
        {kpis.map(k => (
          <div key={k.label} className="stat-card" style={{ position: "relative", overflow: "hidden" }}>
            <div style={{ position: "absolute", top: 0, left: 0, width: "100%", height: 3, background: `linear-gradient(90deg, ${k.color}, ${k.color}44)` }} />
            <div className="flex items-center gap-2">
              <span style={{ width: 8, height: 8, borderRadius: "50%", backgroundColor: k.color, display: "inline-block" }} />
              <p className="text-xs font-medium" style={{ color: "var(--text-secondary)" }}>{k.label}</p>
            </div>
            <p className="text-3xl font-bold mt-2 tabular-nums" style={{ color: k.color }}>{k.value}</p>
            {k.hint && <p className="text-[11px] mt-1" style={{ color: "var(--text-tertiary)" }}>{k.hint}</p>}
          </div>
        ))}
      </div>

      {/* 图表区 */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <Card title="评分分布" icon="📊" color="#3b82f6">
          <BarChart data={scoreBuckets} color="#3b82f6" />
        </Card>
        <Card title="采购等级分布" icon="🛒" color="#10b981">
          <DonutChart data={levelDist.filter(d => d.value > 0)} />
        </Card>
        <Card title="生命周期分布" icon="🔄" color="#8b5cf6">
          <BarChart data={lifecycle} color="#8b5cf6" />
        </Card>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
        <Card title="断货风险 TOP" icon="🚨" color="#ef4444">
          <RiskList items={stockout} warn />
        </Card>
        <Card title="库存积压 TOP" icon="📦" color="#f59e0b">
          <RiskList items={overstock} />
        </Card>
        <Card title="产品等级分布" icon="🏷️" color="#06b6d4">
          <BarChart data={levels} color="#06b6d4" />
        </Card>
      </div>

      {/* 重点提醒 */}
      <div className="card">
        <div className="flex items-center gap-2 mb-3">
          <span style={{ width: 4, height: 16, borderRadius: 2, background: "linear-gradient(180deg, #f97316, #f9731666)", display: "inline-block" }} />
          <span className="text-sm">🔔</span>
          <h3 className="font-semibold text-sm" style={{ color: "var(--text-primary)" }}>每日重点提醒 TOP</h3>
        </div>
        {alerts.length === 0 && <p style={{ color: "var(--text-tertiary)" }}>暂无重点提醒</p>}
        {alerts.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table className="w-full text-sm">
              <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th className="text-left py-2 pr-3">ASIN</th><th className="text-left py-2 pr-3">产品</th>
                <th className="text-center py-2 pr-3">提醒</th><th className="text-center py-2 pr-3">级别</th>
                <th className="text-right py-2 pr-3">评分</th><th className="text-right py-2">建议量</th>
              </tr></thead>
              <tbody>
                {alerts.slice(0, 12).map(a => (
                  <tr key={a.asin} style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <td className="py-2 pr-3 font-mono">{a.asin}</td>
                    <td className="py-2 pr-3 truncate max-w-xs">{a.product_name || "-"}</td>
                    <td className="py-2 pr-3 text-center">{a.alert_type || "-"}</td>
                    <td className="py-2 pr-3 text-center">{a.purchase_level || "-"}</td>
                    <td className="py-2 pr-3 text-right">{a.purchase_score ?? "-"}</td>
                    <td className="py-2 text-right font-mono">{a.suggested_qty?.toLocaleString() ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
