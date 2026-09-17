"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type InventoryHealthItem, type InventoryHealthResponse } from "@/lib/api";

interface ColumnDef {
  key: keyof InventoryHealthItem;
  label: string;
  defaultVisible: boolean;
  align?: "left" | "right" | "center";
}

const COLUMNS: ColumnDef[] = [
  { key: "asin", label: "ASIN", defaultVisible: true },
  { key: "product_name", label: "品名", defaultVisible: true },
  { key: "primary_operator", label: "主运营负责人", defaultVisible: true },
  { key: "available_stock", label: "可用库存", defaultVisible: true, align: "right" },
  { key: "inventory_days", label: "库存天数", defaultVisible: true, align: "right" },
  { key: "urgency_level", label: "紧急程度", defaultVisible: true },
  { key: "replenishment_cycle", label: "补货周期", defaultVisible: true, align: "right" },
  { key: "purchase_trigger", label: "触发", defaultVisible: true },
  { key: "urgency_score", label: "紧急评分", defaultVisible: false, align: "right" },
  { key: "suggested_qty", label: "建议数量", defaultVisible: false, align: "right" },
  { key: "purchase_score", label: "评分", defaultVisible: false, align: "right" },
  { key: "purchase_level", label: "级别", defaultVisible: false },
  { key: "product_level", label: "产品等级", defaultVisible: false },
  { key: "life_cycle", label: "生命周期", defaultVisible: false },
  { key: "product_type", label: "产品类型", defaultVisible: false },
  { key: "forecast_total", label: "预测总销量", defaultVisible: false, align: "right" },
  { key: "calc_date", label: "计算日期", defaultVisible: false },
];

const STORAGE_KEY = "inventory-column-visibility";
const PAGE_SIZE = 200;

const URGENCY_STYLE: Record<string, { color: string; bg: string }> = {
  危险: { color: "var(--accent-red)", bg: "rgba(239,68,68,0.12)" },
  偏低: { color: "var(--accent-orange)", bg: "rgba(249,115,22,0.12)" },
  健康: { color: "var(--accent-green)", bg: "rgba(34,197,94,0.12)" },
  过量: { color: "var(--text-secondary)", bg: "var(--bg-tertiary)" },
};

function loadVisibility(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return Object.fromEntries(COLUMNS.map(c => [c.key, c.defaultVisible]));
}

function cellValue(item: InventoryHealthItem, key: keyof InventoryHealthItem): string {
  const v = item[key];
  if (v === null || v === undefined) return "-";
  if (key === "available_stock" || key === "suggested_qty" || key === "forecast_total") return Number(v).toLocaleString();
  if (key === "calc_date") return String(v).slice(0, 10);
  if (key === "inventory_days") return v === 999 ? "无销售" : String(v);
  return String(v);
}

export default function InventoryPage() {
  const [data, setData] = useState<InventoryHealthResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [debounced, setDebounced] = useState("");
  const [operatorFilter, setOperatorFilter] = useState("");
  const [operatorNames, setOperatorNames] = useState<string[]>([]);
  const [urgencyFilter, setUrgencyFilter] = useState("");
  const [levelFilter, setLevelFilter] = useState("");
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<Record<string, boolean>>(loadVisibility);
  const [showColumns, setShowColumns] = useState(false);
  const [thresholds, setThresholds] = useState<{ danger: number; low: number; healthy: number }>({ danger: 15, low: 30, healthy: 90 });
  const [draft, setDraft] = useState<{ danger: string; low: string; healthy: string }>({ danger: "15", low: "30", healthy: "90" });
  const [saving, setSaving] = useState(false);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const load = useCallback(() => {
    setLoading(true);
    api.calculation
      .inventoryHealth({
        keyword: debounced || undefined,
        operator: operatorFilter || undefined,
        urgency: urgencyFilter || undefined,
        purchase_level: levelFilter || undefined,
        skip: page * PAGE_SIZE,
        limit: PAGE_SIZE,
      })
      .then(res => {
        setData(res);
        if (res.thresholds) {
          setThresholds(res.thresholds);
          setDraft({
            danger: String(res.thresholds.danger),
            low: String(res.thresholds.low),
            healthy: String(res.thresholds.healthy),
          });
        }
      })
      .catch((e: Error) => {
        setData(null);
        setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [debounced, operatorFilter, urgencyFilter, levelFilter, page]);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    api.operators.distinctNames().then(r => setOperatorNames(r.names)).catch(() => setOperatorNames([]));
  }, []);

  const saveThresholds = async () => {
    setSaving(true);
    setSavedMsg(null);
    try {
      const values = [
        { key: "inventory_danger_max_days", value: Number(draft.danger) },
        { key: "inventory_low_max_days", value: Number(draft.low) },
        { key: "inventory_healthy_max_days", value: Number(draft.healthy) },
      ];
      for (const v of values) {
        if (!Number.isFinite(v.value) || v.value < 0) throw new Error(`${v.key} 必须是大于等于0的数字`);
        await api.config.update(v.key, String(v.value));
      }
      setSavedMsg("紧急程度阈值已保存，分档立即生效");
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleColumn = (key: keyof InventoryHealthItem) => {
    const next = { ...visibility, [key]: !visibility[key] };
    setVisibility(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const visibleColumns = COLUMNS.filter(c => visibility[c.key]);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const { danger, low, healthy } = thresholds;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">库存健康分析</h1>

      <div className="card mb-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h2 className="text-lg font-semibold">库存紧急程度</h2>
          <div className="flex items-center gap-2 text-sm">
            <label className="flex items-center gap-1">
              危险&lt;<input
                type="number"
                value={draft.danger}
                min={0}
                onChange={e => setDraft(d => ({ ...d, danger: e.target.value }))}
                className="px-2 py-1 rounded border w-20 text-right"
                style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />天
            </label>
            <label className="flex items-center gap-1">
              偏低&lt;<input
                type="number"
                value={draft.low}
                min={0}
                onChange={e => setDraft(d => ({ ...d, low: e.target.value }))}
                className="px-2 py-1 rounded border w-20 text-right"
                style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />天
            </label>
            <label className="flex items-center gap-1">
              健康≤<input
                type="number"
                value={draft.healthy}
                min={0}
                onChange={e => setDraft(d => ({ ...d, healthy: e.target.value }))}
                className="px-2 py-1 rounded border w-20 text-right"
                style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />天
            </label>
            <button
              onClick={saveThresholds}
              disabled={saving}
              className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: saving ? "#94a3b8" : "var(--accent-green)" }}
            >
              {saving ? "保存中..." : "保存分档"}
            </button>
          </div>
        </div>
        {savedMsg && <p className="text-xs mb-2" style={{ color: "var(--accent-green)" }}>{savedMsg}</p>}
        <div className="grid grid-cols-4 gap-3 text-sm">
          <div className="p-3 rounded-md" style={{ backgroundColor: URGENCY_STYLE.危险.bg }}>
            <span className="font-bold" style={{ color: URGENCY_STYLE.危险.color }}>危险</span>
            <p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>库存 &lt; {danger} 天</p>
          </div>
          <div className="p-3 rounded-md" style={{ backgroundColor: URGENCY_STYLE.偏低.bg }}>
            <span className="font-bold" style={{ color: URGENCY_STYLE.偏低.color }}>偏低</span>
            <p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>{danger} - {low - 1} 天</p>
          </div>
          <div className="p-3 rounded-md" style={{ backgroundColor: URGENCY_STYLE.健康.bg }}>
            <span className="font-bold" style={{ color: URGENCY_STYLE.健康.color }}>健康</span>
            <p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>{low} - {healthy} 天</p>
          </div>
          <div className="p-3 rounded-md" style={{ backgroundColor: URGENCY_STYLE.过量.bg }}>
            <span className="font-bold" style={{ color: URGENCY_STYLE.过量.color }}>过量</span>
            <p className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>{healthy} 天以上</p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-4">
        <input
          type="text"
          value={keyword}
          onChange={e => { setKeyword(e.target.value); setPage(0); }}
          placeholder="搜索 ASIN / 品名 / 分类..."
          className="text-sm px-3 py-1.5 rounded-md border w-56"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        />
        <select
          value={operatorFilter}
          onChange={e => { setOperatorFilter(e.target.value); setPage(0); }}
          className="text-sm px-3 py-1.5 rounded-md border"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        >
          <option value="">全部负责人</option>
          {operatorNames.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
        <select
          value={urgencyFilter}
          onChange={e => { setUrgencyFilter(e.target.value); setPage(0); }}
          className="text-sm px-3 py-1.5 rounded-md border"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        >
          <option value="">全部紧急程度</option>
          <option value="危险">危险</option>
          <option value="偏低">偏低</option>
          <option value="健康">健康</option>
          <option value="过量">过量</option>
        </select>
        <select
          value={levelFilter}
          onChange={e => { setLevelFilter(e.target.value); setPage(0); }}
          className="text-sm px-3 py-1.5 rounded-md border"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        >
          <option value="">全部级别</option>
          <option value="立即采购">立即采购</option>
          <option value="观察">观察</option>
          <option value="暂停">暂停</option>
        </select>
        <button
          onClick={() => setShowColumns(!showColumns)}
          className="px-3 py-1.5 rounded-md text-sm font-medium"
          style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
        >
          字段显隐
        </button>
      </div>

      {showColumns && (
        <div className="card mb-4">
          <h3 className="text-sm font-semibold mb-3">可见字段（品名 / 负责人默认可见）</h3>
          <div className="flex flex-wrap gap-4">
            {COLUMNS.map(c => (
              <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={!!visibility[c.key]}
                  onChange={() => toggleColumn(c.key)}
                />
                {c.label}
              </label>
            ))}
          </div>
        </div>
      )}

      {error && <div className="card mb-4" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>{error}</div>}
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        共 {data?.total ?? "-"} 个产品（最近一次计算）
      </p>
      {loading && <p>加载中...</p>}
      {!loading && (!data || data.items.length === 0) && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无数据</div>
      )}
      {data && data.items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                {visibleColumns.map(c => (
                  <th key={c.key} className={`${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"} py-2 pr-3 whitespace-nowrap`}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.items.map(item => (
                <tr key={item.asin} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  {visibleColumns.map(c => {
                    if (c.key === "urgency_level") {
                      const lv = item.urgency_level;
                      const st = lv ? URGENCY_STYLE[lv] : null;
                      return (
                        <td key={c.key} className="py-2 pr-3 whitespace-nowrap">
                          {st && lv ? (
                            <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ color: st.color, backgroundColor: st.bg }}>{lv}</span>
                          ) : "-"}
                        </td>
                      );
                    }
                    if (c.key === "purchase_level") {
                      const lv = item.purchase_level;
                      return (
                        <td key={c.key} className="py-2 pr-3 whitespace-nowrap">
                          {lv && (
                            <span className="px-2 py-0.5 rounded text-xs font-medium"
                              style={{ backgroundColor: lv === "立即采购" ? "#fef3c7" : lv === "观察" ? "#dbeafe" : "#f1f5f9" }}>
                              {lv}
                            </span>
                          )}
                        </td>
                      );
                    }
                    if (c.key === "asin") {
                      return <td key={c.key} className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{item.asin}</td>;
                    }
                    if (c.key === "inventory_days") {
                      const days = item.inventory_days;
                      const color = days != null && days < danger ? "var(--accent-red)" : days != null && days < low ? "var(--accent-orange)" : "inherit";
                      return <td key={c.key} className="py-2 pr-3 text-right font-bold whitespace-nowrap" style={{ color }}>{cellValue(item, c.key)}</td>;
                    }
                    return (
                      <td key={c.key} className={`py-2 pr-3 truncate max-w-xs ${c.align === "right" ? "text-right" : ""}`}>{cellValue(item, c.key)}</td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span style={{ color: "var(--text-tertiary)" }}>
            第 {page + 1} / {totalPages} 页（每页 {PAGE_SIZE} 条）
          </span>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
              className="px-3 py-1.5 rounded-md"
              style={{ backgroundColor: page === 0 ? "var(--bg-tertiary)" : "var(--accent-blue)", color: page === 0 ? "var(--text-tertiary)" : "#fff" }}
            >
              上一页
            </button>
            <button
              disabled={page + 1 >= totalPages}
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              className="px-3 py-1.5 rounded-md"
              style={{ backgroundColor: page + 1 >= totalPages ? "var(--bg-tertiary)" : "var(--accent-blue)", color: page + 1 >= totalPages ? "var(--text-tertiary)" : "#fff" }}
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
