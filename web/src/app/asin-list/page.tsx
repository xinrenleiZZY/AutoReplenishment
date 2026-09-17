"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type AsinListItem, type AsinListTab } from "@/lib/api";

const TABS: { key: AsinListTab; label: string; hint: string }[] = [
  { key: "available", label: "可用ASIN列表", hint: "状态为在售、会参与后续分析/日报的 ASIN" },
  { key: "all", label: "所有ASIN列表", hint: "库中全部 ASIN（含已排除/已删除/停用）" },
  { key: "keep", label: "保留ASIN列表", hint: "人工保留，优先级高于排除，清洗不被覆盖、不会被标记已删除" },
  { key: "exclude", label: "排除ASIN列表", hint: "人工排除，清洗/同步时强制停用并标记「已排除」" },
];

const PAGE_SIZES = [50, 100, 200, 500];

const selectStyle = {
  backgroundColor: "var(--bg-secondary)",
  borderColor: "var(--border-color)",
  color: "var(--text-primary)",
} as const;

export default function AsinListPage() {
  const [tab, setTab] = useState<AsinListTab>("available");
  const [items, setItems] = useState<AsinListItem[]>([]);
  const [total, setTotal] = useState(0);
  const [listUpdatedAt, setListUpdatedAt] = useState("");
  const [lastImportAt, setLastImportAt] = useState("");
  const [keyword, setKeyword] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [newAsins, setNewAsins] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    const t = setTimeout(() => { setDebounced(keyword); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const load = useCallback(() => {
    setLoading(true);
    api.products
      .asinList({ tab, keyword: debounced || undefined, skip: page * pageSize, limit: pageSize })
      .then(res => {
        setItems(res.items);
        setTotal(res.total);
        setListUpdatedAt(res.list_updated_at || "");
        setLastImportAt(res.last_import_at || "");
      })
      .catch(() => { setItems([]); setTotal(0); })
      .finally(() => setLoading(false));
  }, [tab, debounced, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  const switchTab = (key: AsinListTab) => {
    setTab(key);
    setPage(0);
    setKeyword("");
    setNewAsins("");
    setMsg(null);
  };

  const act = async (
    action: "exclude" | "keep" | "remove_exclude" | "remove_keep",
    asins: string[],
    confirmText?: string,
  ) => {
    if (confirmText && !window.confirm(confirmText)) return;
    setBusy(asins.join(","));
    setMsg(null);
    try {
      const res = await api.products.asinListAction(action, asins);
      setMsg(`已提交 ${res.asins.length} 个 ASIN（状态变更 ${res.status_changed} 条），10 分钟内自动刷新状态`);
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  const addAsins = () => {
    const parsed = newAsins
      .split(/[\s,;，；]+/)
      .map(a => a.trim().toUpperCase())
      .filter(Boolean);
    if (parsed.length === 0) {
      setMsg("请输入 ASIN");
      return;
    }
    act(tab === "exclude" ? "exclude" : "keep", parsed);
    setNewAsins("");
  };

  const renderActions = (item: AsinListItem) => {
    const disabled = busy !== null;
    const btn = (label: string, color: string, onClick: () => void) => (
      <button
        onClick={onClick}
        disabled={disabled}
        className="px-2 py-0.5 rounded text-[11px] font-medium"
        style={{ backgroundColor: "var(--bg-tertiary)", color: disabled ? "var(--text-tertiary)" : color }}
      >
        {label}
      </button>
    );
    if (tab === "keep") {
      return btn("移出保留", "var(--accent-red)", () =>
        act("remove_keep", [item.asin], `确认将 ${item.asin} 移出保留列表？移出后状态由下次清洗重新判定`));
    }
    if (tab === "exclude") {
      return (
        <span className="flex gap-1">
          {btn("加入保留", "var(--accent-green)", () =>
            act("keep", [item.asin], `确认将 ${item.asin} 加入保留列表？将从排除列表移除并恢复在售`))}
          {btn("移出排除", "var(--accent-red)", () =>
            act("remove_exclude", [item.asin], `确认将 ${item.asin} 移出排除列表？移出后状态由下次清洗重新判定`))}
        </span>
      );
    }
    return (
      <span className="flex gap-1">
        {btn("排除", "var(--accent-red)", () =>
          act("exclude", [item.asin], `确认排除 ${item.asin}？将停用并标记「已排除」`))}
        {btn("保留", "var(--accent-green)", () =>
          act("keep", [item.asin], `确认保留 ${item.asin}？保留优先级高于排除`))}
      </span>
    );
  };

  const currentTab = TABS.find(t => t.key === tab)!;
  const showsImportTime = tab === "available" || tab === "all";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">ASIN列表</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>{currentTab.hint}</p>

      <div className="flex flex-wrap gap-2 mb-3">
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => switchTab(t.key)}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{
              backgroundColor: tab === t.key ? "var(--accent-blue)" : "var(--bg-tertiary)",
              color: tab === t.key ? "#fff" : "var(--text-secondary)",
            }}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-3 mb-3">
        <input
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          placeholder="搜索 ASIN / 品名"
          className="px-3 py-1.5 rounded-md text-sm border min-w-[220px]"
          style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        />
        {(tab === "keep" || tab === "exclude") && (
          <>
            <input
              value={newAsins}
              onChange={e => setNewAsins(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") addAsins(); }}
              placeholder="添加 ASIN（逗号/换行分隔），回车提交"
              className="flex-1 min-w-[260px] px-3 py-1.5 rounded-md text-sm border"
              style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
            />
            <button
              onClick={addAsins}
              disabled={busy !== null}
              className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: busy !== null ? "#94a3b8" : "var(--accent-blue)" }}
            >
              添加
            </button>
          </>
        )}
      </div>

      <p className="text-sm mb-3" style={{ color: "var(--text-tertiary)" }}>
        {showsImportTime
          ? `数据更新时间：${lastImportAt || "暂无"}（最近一次产品导入）`
          : `数据更新时间：${listUpdatedAt || "暂无"}（最近一次手动操作）`}
        {" ｜ "}
        共 {total} 个 ASIN
        {msg && <span style={{ color: "var(--accent-green)" }}>{" ｜ "}{msg}</span>}
      </p>

      {loading && <p>加载中...</p>}
      {!loading && items.length === 0 && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无 ASIN</div>
      )}
      {!loading && items.length > 0 && (
        <div className="card overflow-auto" style={{ maxHeight: "calc(100vh - 240px)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th className="text-left py-2 pr-3 whitespace-nowrap">ASIN</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">品名</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">分类</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">负责人</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">生命周期</th>
                <th className="text-center py-2 pr-3 whitespace-nowrap">等级</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">状态</th>
                <th className="text-left py-2 pr-3 whitespace-nowrap">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map(item => (
                <tr key={item.asin} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{item.asin}</td>
                  <td className="py-2 pr-3 truncate max-w-xs">{item.product_name || "-"}</td>
                  <td className="py-2 pr-3 truncate max-w-[160px]">{item.category || "-"}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">{item.operator || "-"}</td>
                  <td className="py-2 pr-3 whitespace-nowrap">{item.life_cycle || "-"}</td>
                  <td className="py-2 pr-3 text-center">{item.product_level || "-"}</td>
                  <td className="py-2 pr-3 whitespace-nowrap text-xs">
                    {item.in_db ? (
                      <span className="inline-flex items-center gap-1">
                        <span className={`inline-block w-2 h-2 rounded-full ${item.status ? "bg-green-500" : "bg-red-400"}`} />
                        {item.status_text || (item.status ? "在售" : "停用")}
                      </span>
                    ) : (
                      <span style={{ color: "var(--text-tertiary)" }}>库中不存在</span>
                    )}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">{renderActions(item)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > pageSize && (
        <div className="flex items-center justify-between mt-4 text-sm flex-wrap gap-3">
          <div className="flex items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
            <span>每页</span>
            <select
              value={pageSize}
              onChange={e => { setPageSize(Number(e.target.value)); setPage(0); }}
              className="px-2 py-1 rounded border text-xs"
              style={selectStyle}
            >
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n} 条</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span style={{ color: "var(--text-tertiary)" }}>第 {page + 1} / {totalPages} 页</span>
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
