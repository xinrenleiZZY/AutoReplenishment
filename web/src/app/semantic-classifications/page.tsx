"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type SemanticClassificationPage } from "@/lib/api";

const PAGE_SIZES = [20, 50, 100, 200, 500];

function fmtTime(iso?: string | null) {
  if (!iso) return "-";
  return iso.replace("T", " ").slice(0, 19);
}

export default function SemanticClassificationsPage() {
  const [data, setData] = useState<SemanticClassificationPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.semanticClassifications
      .list({ skip: page * pageSize, limit: pageSize })
      .then(setData)
      .catch((e: Error) => { setError(e.message); setData(null); })
      .finally(() => setLoading(false));
  }, [page, pageSize]);

  useEffect(() => { load(); }, [load]);

  const selectStyle: React.CSSProperties = {
    backgroundColor: "var(--bg-secondary)",
    borderColor: "var(--border-color)",
    color: "var(--text-primary)",
  };

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));

  // 分页超出当前结果范围时回退到最后一页
  useEffect(() => {
    if (!data) return;
    const tp = Math.max(1, Math.ceil(data.total / pageSize));
    if (page > tp - 1) setPage(tp - 1);
  }, [data, page, pageSize]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold">缓存天数语义分类</h1>
        <button
          onClick={load}
          className="px-3 py-1.5 rounded-md text-sm font-medium"
          style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
        >
          刷新
        </button>
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        由 AI 分析亚马逊标题（listing_title）判定「装饰品 / 非装饰品」，用于缓存天数判定（装饰品 14 天、非装饰品 3 天）。随「基础数据刷新」更新，本页仅展示。
      </p>

      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>共 {total} 条</p>

      {loading && <p>加载中...</p>}
      {error && <p className="text-sm" style={{ color: "var(--accent-red)" }}>{error}</p>}
      {!loading && !error && items.length === 0 && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>
          暂无数据：请先执行「基础数据刷新」生成语义分类。
        </div>
      )}
      {!loading && items.length > 0 && (
        <div className="card overflow-auto" style={{ maxHeight: "calc(100vh - 240px)" }}>
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3 whitespace-nowrap">ASIN</th>
              <th className="text-left py-2 pr-3 whitespace-nowrap">亚马逊标题</th>
              <th className="text-left py-2 pr-3 whitespace-nowrap">语义分类</th>
              <th className="text-left py-2 whitespace-nowrap">更新时间</th>
            </tr></thead>
            <tbody>{items.map(item => (
              <tr
                key={item.asin}
                style={{ borderBottom: "1px solid var(--border-color)" }}
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}
              >
                <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{item.asin}</td>
                <td className="py-2 pr-3 truncate max-w-md">{item.listing_title || "-"}</td>
                <td className="py-2 pr-3 whitespace-nowrap">{item.semantic_classification || "-"}</td>
                <td className="py-2 font-mono text-xs whitespace-nowrap">{fmtTime(item.updated_at)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}

      {total > 0 && (
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
