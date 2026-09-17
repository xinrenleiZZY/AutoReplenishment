"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type SyncOverview } from "@/lib/api";

const STATUS_META: Record<string, { text: string; bg: string; color: string }> = {
  ok: { text: "正常", bg: "#dcfce7", color: "#166534" },
  warn: { text: "异常", bg: "#fef9c3", color: "#a16207" },
  none: { text: "无数据", bg: "#f1f5f9", color: "#64748b" },
};

function fmtTime(iso?: string | null) {
  if (!iso) return "-";
  return iso.replace("T", " ").slice(0, 19);
}

function TaskStatusBadge({ status }: { status: string | null }) {
  const meta =
    status === "success" ? { bg: "#dcfce7", color: "#166534", text: "成功" }
    : status === "failed" ? { bg: "#fee2e2", color: "#dc2626", text: "失败" }
    : status === "running" ? { bg: "#dbeafe", color: "#1e40af", text: "运行中" }
    : { bg: "#f1f5f9", color: "#64748b", text: "未执行" };
  return (
    <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: meta.bg, color: meta.color }}>
      {meta.text}
    </span>
  );
}

export default function SyncDataPage() {
  const [data, setData] = useState<SyncOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    api.syncOverview()
      .then(setData)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold">今日同步数据</h1>
        <div className="flex items-center gap-3">
          {data && <span className="text-sm font-mono" style={{ color: "var(--text-tertiary)" }}>{data.date}</span>}
          <button
            onClick={load}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
          >
            刷新
          </button>
        </div>
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        展示今日各数据源同步到的最新内容与执行状态，方便快速确认数据是否更新到位。
      </p>

      {loading && <p>加载中...</p>}
      {error && (
        <div className="card text-center py-12" style={{ color: "var(--accent-red)" }}>
          加载失败：{error}
        </div>
      )}

      {data && (
        <>
          <h2 className="text-sm font-semibold mb-3">数据源概览</h2>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 mb-6">
            {data.sources.map(s => {
              const meta = STATUS_META[s.status] || STATUS_META.none;
              return (
                <div key={s.key} className="card p-4">
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm" style={{ color: "var(--text-secondary)" }}>{s.label}</span>
                    <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: meta.bg, color: meta.color }}>
                      {meta.text}
                    </span>
                  </div>
                  <div className="text-3xl font-bold" style={{ color: "var(--text-primary)" }}>{s.count}</div>
                  {s.detail && <div className="text-xs mt-1" style={{ color: "var(--text-tertiary)" }}>{s.detail}</div>}
                </div>
              );
            })}
          </div>

          <h2 className="text-sm font-semibold mb-3">今日同步任务</h2>
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <th className="text-left py-2 pr-3">任务</th>
                  <th className="text-center py-2 pr-3">状态</th>
                  <th className="text-right py-2 pr-3">总记录</th>
                  <th className="text-right py-2 pr-3">成功</th>
                  <th className="text-left py-2 pr-3">开始</th>
                  <th className="text-left py-2">完成</th>
                </tr>
              </thead>
              <tbody>
                {data.tasks.map(t => (
                  <tr key={t.sync_type} style={{ borderBottom: "1px solid var(--border-color)" }}>
                    <td className="py-2 pr-3">{t.label}</td>
                    <td className="py-2 pr-3 text-center"><TaskStatusBadge status={t.status} /></td>
                    <td className="py-2 pr-3 text-right">{t.total_count ?? "-"}</td>
                    <td className="py-2 pr-3 text-right">{t.success_count ?? "-"}</td>
                    <td className="py-2 pr-3 font-mono text-xs">{fmtTime(t.started_at)}</td>
                    <td className="py-2 font-mono text-xs">{fmtTime(t.completed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.tasks.some(t => t.error_message) && (
            <div className="card mt-4">
              <h2 className="text-lg font-semibold mb-3">错误详情</h2>
              {data.tasks.filter(t => t.error_message).map(t => (
                <p key={t.sync_type} className="text-xs mb-2 whitespace-pre-wrap" style={{ color: "var(--accent-red)" }}>
                  [{t.label} {fmtTime(t.started_at)}] {t.error_message}
                </p>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
