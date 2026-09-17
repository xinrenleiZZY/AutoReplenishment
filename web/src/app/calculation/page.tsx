"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  api,
  type BatchStats,
  type DueStats,
  type OverviewItem,
  type OverviewResponse,
} from "@/lib/api";

interface ColumnDef {
  key: keyof OverviewItem;
  label: string;
  defaultVisible: boolean;
}

const COLUMNS: ColumnDef[] = [
  { key: "asin", label: "ASIN", defaultVisible: true },
  { key: "product_name", label: "品名", defaultVisible: true },
  { key: "product_stage", label: "新老品", defaultVisible: true },
  { key: "primary_operator", label: "主运营负责人", defaultVisible: true },
  { key: "purchase_score", label: "评分", defaultVisible: true },
  { key: "base_score", label: "原始分", defaultVisible: true },
  { key: "purchase_level", label: "级别", defaultVisible: true },
  { key: "suggested_qty", label: "建议数量", defaultVisible: true },
  { key: "inventory_days", label: "库存天数", defaultVisible: true },
  { key: "purchase_trigger", label: "触发", defaultVisible: true },
  { key: "product_level", label: "产品等级", defaultVisible: false },
  { key: "life_cycle", label: "生命周期", defaultVisible: false },
  { key: "product_type", label: "产品类型", defaultVisible: false },
  { key: "available_stock", label: "可用库存", defaultVisible: false },
  { key: "replenishment_cycle", label: "补货周期", defaultVisible: false },
  { key: "calc_date", label: "计算日期", defaultVisible: false },
];

const STORAGE_KEY = "calc-column-visibility";
const PAGE_SIZES = [20, 30, 50, 100, 200];

function loadVisibility(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return Object.fromEntries(COLUMNS.map(c => [c.key, c.defaultVisible]));
}

// ── 列表状态持久化（点开 ASIN 详情后返回时恢复筛选/分页） ──
const STATE_STORAGE_KEY = "calc-list-state";

interface ListState {
  keyword: string;
  operator: string;
  productLevel: string;
  lifeCycle: string;
  level: string;
  calcDate: string;
  page: number;
  pageSize: number;
}

/** 从 sessionStorage 恢复列表状态；无记录时用默认值 */
function loadListState(): ListState {
  const fallback: ListState = {
    keyword: "",
    operator: "",
    productLevel: "",
    lifeCycle: "",
    level: "",
    calcDate: "",
    page: 0,
    pageSize: 200,
  };
  try {
    const raw = sessionStorage.getItem(STATE_STORAGE_KEY);
    if (!raw) return fallback;
    const p = JSON.parse(raw) as Partial<ListState>;
    const str = (v: unknown) => (typeof v === "string" ? v : "");
    return {
      keyword: str(p.keyword),
      operator: str(p.operator),
      productLevel: str(p.productLevel),
      lifeCycle: str(p.lifeCycle),
      level: str(p.level),
      calcDate: str(p.calcDate),
      page: Number.isInteger(p.page) && (p.page as number) >= 0 ? (p.page as number) : 0,
      pageSize: PAGE_SIZES.includes(p.pageSize as number) ? (p.pageSize as number) : 200,
    };
  } catch {
    return fallback;
  }
}

function cellValue(item: OverviewItem, key: keyof OverviewItem): string {
  const v = item[key];
  if (v === null || v === undefined) return "-";
  if (key === "suggested_qty" || key === "available_stock") return Number(v).toLocaleString();
  if (key === "purchase_score" || key === "base_score") return String(v);
  if (key === "calc_date") return String(v).slice(0, 10);
  return String(v);
}

export default function CalculationPage() {
  const router = useRouter();
  const [initial] = useState<ListState>(loadListState);
  const [data, setData] = useState<OverviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [keyword, setKeyword] = useState(initial.keyword);
  const [debounced, setDebounced] = useState(initial.keyword.trim());
  const [operatorFilter, setOperatorFilter] = useState(initial.operator);
  const [operatorNames, setOperatorNames] = useState<string[]>([]);
  const [productLevelFilter, setProductLevelFilter] = useState(initial.productLevel);
  const [lifeCycleFilter, setLifeCycleFilter] = useState(initial.lifeCycle);
  const [levelFilter, setLevelFilter] = useState(initial.level);
  const [calcDateFilter, setCalcDateFilter] = useState(initial.calcDate);
  const [page, setPage] = useState(initial.page);
  const [pageSize, setPageSize] = useState(initial.pageSize);
  const [batchStats, setBatchStats] = useState<BatchStats | null>(null);
  const [dueStats, setDueStats] = useState<DueStats | null>(null);
  const [progress, setProgress] = useState<{ total: number; done: number; percent: number; current_asin: string | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<Record<string, boolean>>(loadVisibility);
  const [showColumns, setShowColumns] = useState(false);
  const [analyzing, setAnalyzing] = useState<Record<string, boolean>>({});
  const [feedbackTarget, setFeedbackTarget] = useState<OverviewItem | null>(null);
  const [feedbackText, setFeedbackText] = useState("");
  const [feedbackSaving, setFeedbackSaving] = useState(false);
  const [adopting, setAdopting] = useState<Record<string, boolean>>({});
  const [excludeTarget, setExcludeTarget] = useState<OverviewItem | null>(null);
  const [excludeSaving, setExcludeSaving] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const load = useCallback(() => {
    setLoading(true);
    api.calculation
      .overview({
        keyword: debounced || undefined,
        operator: operatorFilter || undefined,
        product_level: productLevelFilter || undefined,
        life_cycle: lifeCycleFilter || undefined,
        purchase_level: levelFilter || undefined,
        calc_date: calcDateFilter || undefined,
        skip: page * pageSize,
        limit: pageSize,
      })
      .then(setData)
      .catch((e: Error) => {
        setData(null);
        setError(e.message);
      })
      .finally(() => setLoading(false));
  }, [debounced, operatorFilter, productLevelFilter, lifeCycleFilter, levelFilter, calcDateFilter, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  // 列表状态写入 sessionStorage：点开 ASIN 详情返回后可恢复筛选与分页
  useEffect(() => {
    try {
      sessionStorage.setItem(
        STATE_STORAGE_KEY,
        JSON.stringify({
          keyword,
          operator: operatorFilter,
          productLevel: productLevelFilter,
          lifeCycle: lifeCycleFilter,
          level: levelFilter,
          calcDate: calcDateFilter,
          page,
          pageSize,
        }),
      );
    } catch {
      /* ignore */
    }
  }, [keyword, operatorFilter, productLevelFilter, lifeCycleFilter, levelFilter, calcDateFilter, page, pageSize]);

  // 恢复的分页超出当前结果范围时回退到最后一页
  useEffect(() => {
    if (!data) return;
    const tp = Math.max(1, Math.ceil(data.total / pageSize));
    if (page > tp - 1) setPage(tp - 1);
  }, [data, page, pageSize]);

  useEffect(() => {
    api.operators.distinctNames().then(r => setOperatorNames(r.names)).catch(() => setOperatorNames([]));
  }, []);

  useEffect(() => {
    api.calculation.dueStats().then(setDueStats).catch(() => setDueStats(null));
  }, []);

  const triggerDue = async () => {
    setRunning(true);
    setError(null);
    setProgress({ total: 0, done: 0, percent: 0, current_asin: null });
    try {
      const res = await api.calculation.triggerDue();
      for (;;) {
        const job = await api.calculation.getJob(res.job_id);
        if (job.progress) setProgress(job.progress);
        if (job.status !== "running") break;
        await new Promise(r => setTimeout(r, 2000));
      }
      const job = await api.calculation.getJob(res.job_id);
      if (job.status === "failed") throw new Error(job.error || "计算失败");
      setBatchStats(job.stats);
      api.calculation.dueStats().then(setDueStats).catch(() => null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "按频率计算失败");
    } finally {
      setRunning(false);
      setProgress(null);
    }
  };

  const triggerBatch = async () => {
    setRunning(true);
    setError(null);
    setProgress({ total: 0, done: 0, percent: 0, current_asin: null });
    try {
      const res = await api.calculation.triggerBatch();
      for (;;) {
        const job = await api.calculation.getJob(res.job_id);
        if (job.progress) setProgress(job.progress);
        if (job.status !== "running") break;
        await new Promise(r => setTimeout(r, 2000));
      }
      const job = await api.calculation.getJob(res.job_id);
      if (job.status === "failed") throw new Error(job.error || "计算失败");
      setBatchStats(job.stats);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "批量计算失败");
    } finally {
      setRunning(false);
      setProgress(null);
    }
  };

  const toggleColumn = (key: keyof OverviewItem) => {
    const next = { ...visibility, [key]: !visibility[key] };
    setVisibility(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const analyzeOne = async (asin: string) => {
    setAnalyzing(prev => ({ ...prev, [asin]: true }));
    setError(null);
    setNotice(null);
    try {
      const res = await api.calculation.trigger(asin);
      const notify = res.notify;
      if (notify?.sent) {
        setNotice(`分析完成，已私发结果给负责人「${notify.operator}」`);
      } else if (notify?.reason) {
        setNotice(`分析完成，未私发：${notify.reason}`);
      } else {
        setNotice("分析完成");
      }
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "计算失败");
    } finally {
      setAnalyzing(prev => ({ ...prev, [asin]: false }));
    }
  };

  const openFeedback = (item: OverviewItem) => {
    setFeedbackTarget(item);
    setFeedbackText(item.user_feedback || "");
  };

  const saveFeedback = async () => {
    if (!feedbackTarget) return;
    if (!feedbackText.trim()) {
      setError("反馈内容不能为空");
      return;
    }
    setFeedbackSaving(true);
    setError(null);
    try {
      await api.calculation.feedback({
        asin: feedbackTarget.asin,
        calc_date: feedbackTarget.calc_date ? feedbackTarget.calc_date.slice(0, 10) : undefined,
        feedback: feedbackText.trim(),
        operator: feedbackTarget.primary_operator || undefined,
      });
      setFeedbackTarget(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "反馈保存失败");
    } finally {
      setFeedbackSaving(false);
    }
  };

  const adopt = async (item: OverviewItem) => {
    const key = item.asin + (item.calc_date || "");
    setAdopting(prev => ({ ...prev, [key]: true }));
    setError(null);
    setNotice(null);
    try {
      await api.calculation.adopt({
        asin: item.asin,
        calc_date: item.calc_date ? item.calc_date.slice(0, 10) : undefined,
        operator: item.primary_operator || undefined,
        adopted: !item.adopted,
        confidence: 95,
      });
      setNotice(`已${item.adopted ? "取消" : "采纳"} ${item.asin}${item.calc_date ? `（${item.calc_date.slice(0, 10)}）` : ""} 的分析结果`);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "采纳失败");
    } finally {
      setAdopting(prev => ({ ...prev, [key]: false }));
    }
  };

  const confirmExclude = async () => {
    if (!excludeTarget) return;
    setExcludeSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await api.products.asinListAction("exclude", [excludeTarget.asin]);
      setNotice(`已排除 ${excludeTarget.asin}（状态变更 ${res.status_changed} 条），10 分钟内自动刷新状态`);
      setExcludeTarget(null);
      load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "排除失败");
    } finally {
      setExcludeSaving(false);
    }
  };

  const visibleColumns = COLUMNS.filter(c => visibility[c.key]);
  const totalPages = data ? Math.max(1, Math.ceil(data.total / pageSize)) : 1;
  const levelOrder = ["S", "A", "B", "C", "D"];
  const levelSummary = dueStats
    ? levelOrder
        .filter(lv => dueStats.by_level[lv])
        .map(lv => {
          const info = dueStats.by_level[lv];
          return `${lv}级 ${info.due}/${info.total}（${info.frequency_days}天）`;
        })
        .join(" ｜ ")
    : "";

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold">计算结果</h1>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            type="text"
            value={keyword}
            onChange={e => { setKeyword(e.target.value); setPage(0); }}
            placeholder="搜索 ASIN / 品名 / 分类..."
            className="text-sm px-3 py-1.5 rounded-md border w-56"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          />
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
            value={productLevelFilter}
            onChange={e => { setProductLevelFilter(e.target.value); setPage(0); }}
            className="text-sm px-3 py-1.5 rounded-md border"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          >
            <option value="">全部产品等级</option>
            {["S", "A", "B", "C", "D"].map(l => <option key={l} value={l}>{l} 级</option>)}
          </select>
          <select
            value={lifeCycleFilter}
            onChange={e => { setLifeCycleFilter(e.target.value); setPage(0); }}
            className="text-sm px-3 py-1.5 rounded-md border"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          >
            <option value="">全部生命周期</option>
            {["启动期", "增长期", "热卖期", "成熟期", "下降期", "未知"].map(l => <option key={l} value={l}>{l}</option>)}
          </select>
          <input
            type="date"
            value={calcDateFilter}
            onChange={e => { setCalcDateFilter(e.target.value); setPage(0); }}
            title="按计算日期查询（单日），留空显示最近一次结果"
            className="text-sm px-3 py-1.5 rounded-md border"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          />
          <button
            onClick={() => setShowColumns(!showColumns)}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
          >
            字段显隐
          </button>
          <button
            onClick={triggerDue}
            disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-green)" }}
          >
            {running ? "计算中..." : "按频率计算"}
          </button>
          <button
            onClick={triggerBatch}
            disabled={running}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-blue)" }}
          >
            全量重算
          </button>
        </div>
      </div>

      {showColumns && (
        <div className="card mb-4">
          <h3 className="text-sm font-semibold mb-3">可见字段（默认：ASIN / 品名 / 评分 / 级别 / 建议数量 / 库存天数 / 触发）</h3>
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

      {dueStats && (
        <div className="card mb-4 text-sm">
          <p style={{ color: "var(--text-tertiary)" }}>
            今日到期 <strong>{dueStats.due}</strong> / {dueStats.total} 个产品
          </p>
          {levelSummary && <p className="mt-1" style={{ color: "var(--text-secondary)" }}>{levelSummary}</p>}
        </div>
      )}

      {error && <div className="card mb-4" style={{ borderLeft: "4px solid var(--accent-red)", color: "var(--accent-red)" }}>{error}</div>}
      {notice && <div className="card mb-4" style={{ borderLeft: "4px solid var(--accent-green)", color: "var(--text-primary)" }}>{notice}</div>}
      {progress && progress.total > 0 && (
        <div className="card mb-4 text-sm">
          <div className="flex justify-between text-xs mb-1" style={{ color: "var(--text-tertiary)" }}>
            <span>计算进度 {progress.done}/{progress.total}</span>
            <span className="font-mono">{progress.percent}%</span>
          </div>
          <div style={{ height: 8, borderRadius: 4, backgroundColor: "var(--bg-tertiary)", overflow: "hidden" }}>
            <div style={{ height: "100%", width: `${progress.percent}%`, backgroundColor: "var(--accent-green)", transition: "width .5s" }} />
          </div>
          {progress.current_asin && (
            <p className="text-[10px] font-mono mt-1 truncate" style={{ color: "var(--text-tertiary)" }}>
              正在计算: {progress.current_asin}
            </p>
          )}
        </div>
      )}
      {batchStats && (
        <div className="card mb-4 text-sm" style={{ borderLeft: "4px solid var(--accent-green)" }}>
          计算完成：到期 <strong>{batchStats.due ?? batchStats.total}</strong> / 跳过 <strong>{batchStats.skipped ?? 0}</strong>，
          成功 <strong>{batchStats.success}</strong> / 失败 <strong>{batchStats.failed}</strong>，
          🛒 立即采购 <strong>{batchStats.immediate}</strong> ｜ 👀 观察 <strong>{batchStats.observe}</strong> ｜ ⏸ 暂停 <strong>{batchStats.pause}</strong>
        </div>
      )}

      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        共 {data?.total ?? "-"} 个产品（全部 ASIN，含未计算的原始数据）
      </p>
      {loading && <p>加载中...</p>}
      {!loading && (!data || data.items.length === 0) && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无数据</div>
      )}
      {data && data.items.length > 0 && (
        <div className="card overflow-auto" style={{ maxHeight: "calc(100vh - 240px)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                {visibleColumns.map(c => (
                  <th key={c.key} className="text-left py-2 pr-3 whitespace-nowrap">{c.label}</th>
                ))}
                <th className="text-center py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map(item => (
                <tr
                  key={item.asin}
                  style={{ borderBottom: "1px solid var(--border-color)" }}
                  onClick={() => router.push(`/calculation/${item.asin}`)}
                  className="cursor-pointer"
                  onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                  onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}
                >
                  {visibleColumns.map(c => {
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
                    if (c.key === "purchase_score") {
                      return (
                        <td key={c.key} className="py-2 pr-3 font-bold">{item.purchase_score ?? "-"}</td>
                      );
                    }
                    if (c.key === "asin") {
                      return <td key={c.key} className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{item.asin}</td>;
                    }
                    return (
                      <td key={c.key} className="py-2 pr-3 truncate max-w-xs">{cellValue(item, c.key)}</td>
                    );
                  })}
                  <td className="py-2 text-center" onClick={e => e.stopPropagation()}>
                    <div className="flex gap-1 justify-center">
                      <button
                        onClick={() => analyzeOne(item.asin)}
                        disabled={analyzing[item.asin]}
                        className="px-2 py-1 rounded text-xs font-medium text-white"
                        style={{ backgroundColor: analyzing[item.asin] ? "#94a3b8" : "var(--accent-green)" }}
                      >
                        {analyzing[item.asin] ? "分析中" : "立即分析"}
                      </button>
                      <button
                        onClick={() => openFeedback(item)}
                        className="px-2 py-1 rounded text-xs font-medium"
                        style={{ backgroundColor: item.user_feedback ? "var(--accent-yellow, #fbbf24)" : "var(--bg-tertiary)", color: item.user_feedback ? "#1f2937" : "var(--text-primary)" }}
                        title={item.user_feedback ? `已有反馈：${item.user_feedback}` : "写入/修改对该 ASIN 该日期计算结果的反馈"}
                      >
                        {item.user_feedback ? "反馈✓" : "反馈"}
                      </button>
                      <button
                        onClick={() => adopt(item)}
                        disabled={adopting[item.asin + (item.calc_date || "")]}
                        className="px-2 py-1 rounded text-xs font-medium"
                        style={{ backgroundColor: item.adopted ? "var(--accent-green)" : "var(--bg-tertiary)", color: item.adopted ? "#fff" : "var(--text-primary)" }}
                        title={item.adopted
                          ? `已采纳（${item.adopted_confidence ?? 95}% 正确率）${item.adopted_at ? `，时间 ${item.adopted_at.slice(0, 16)}` : ""}${item.adopted_by ? `，操作人 ${item.adopted_by}` : ""}`
                          : "确认该日该 ASIN 分析已采纳（正确率达到 95%）"}
                      >
                        {adopting[item.asin + (item.calc_date || "")] ? "提交中" : item.adopted ? "已采纳✓" : "采纳"}
                      </button>
                      <button
                        onClick={() => setExcludeTarget(item)}
                        className="px-2 py-1 rounded text-xs font-medium"
                        style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--accent-red)" }}
                        title="将该 ASIN 加入排除列表（清洗时强制停用并标记「已排除」，不再参与计算/日报）"
                      >
                        排除
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > pageSize && (
        <div className="flex items-center justify-between mt-4 text-sm">
          <span style={{ color: "var(--text-tertiary)" }}>
            第 {page + 1} / {totalPages} 页
          </span>
          <div className="flex items-center gap-2">
            <select
              value={pageSize}
              onChange={e => { setPageSize(Number(e.target.value)); setPage(0); }}
              className="px-2 py-1 rounded border text-xs"
              style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
            >
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n} 条/页</option>)}
            </select>
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

      {excludeTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: "rgba(0,0,0,0.45)" }}
          onClick={() => !excludeSaving && setExcludeTarget(null)}
        >
          <div
            className="card w-full max-w-md p-5"
            style={{ backgroundColor: "var(--bg-secondary)" }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold mb-1">确认排除该 ASIN？</h3>
            <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>
              ASIN: <span className="font-mono">{excludeTarget.asin}</span>
              {excludeTarget.product_name ? ` ｜ ${excludeTarget.product_name}` : ""}
            </p>
            <p className="text-xs mb-4" style={{ color: "var(--accent-red)" }}>
              排除后将写入排除列表，清洗时强制停用并标记「已排除」，不再参与计算与日报，10 分钟内自动刷新状态。
            </p>
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setExcludeTarget(null)}
                disabled={excludeSaving}
                className="px-3 py-1.5 rounded-md text-sm"
                style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
              >
                取消
              </button>
              <button
                onClick={confirmExclude}
                disabled={excludeSaving}
                className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: excludeSaving ? "#94a3b8" : "var(--accent-red)" }}
              >
                {excludeSaving ? "排除中..." : "确认排除"}
              </button>
            </div>
          </div>
        </div>
      )}

      {feedbackTarget && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ backgroundColor: "rgba(0,0,0,0.45)" }}
          onClick={() => !feedbackSaving && setFeedbackTarget(null)}
        >
          <div
            className="card w-full max-w-md p-5"
            style={{ backgroundColor: "var(--bg-secondary)" }}
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-base font-semibold mb-1">结果反馈</h3>
            <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>
              ASIN: <span className="font-mono">{feedbackTarget.asin}</span>
              {feedbackTarget.product_name ? ` ｜ ${feedbackTarget.product_name}` : ""}
              {feedbackTarget.calc_date ? ` ｜ 计算日期 ${feedbackTarget.calc_date.slice(0, 10)}` : ""}
            </p>
            <textarea
              value={feedbackText}
              onChange={e => setFeedbackText(e.target.value)}
              rows={4}
              placeholder="填写对该 ASIN 该日期计算结果的评价/纠偏（如：实际断货时间、市场变化、备货调整意见等），将作为后续 AI 评估的参考依据..."
              className="w-full text-sm p-2 rounded-md border"
              style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
            />
            <div className="flex justify-end gap-2 mt-4">
              <button
                onClick={() => setFeedbackTarget(null)}
                disabled={feedbackSaving}
                className="px-3 py-1.5 rounded-md text-sm"
                style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
              >
                取消
              </button>
              <button
                onClick={saveFeedback}
                disabled={feedbackSaving}
                className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: feedbackSaving ? "#94a3b8" : "var(--accent-green)" }}
              >
                {feedbackSaving ? "保存中..." : "保存反馈"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
