"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SyncLog } from "@/lib/api";

const TYPE_LABEL: Record<string, string> = {
  product: "📦 产品同步",
  sales: "📈 销量同步",
  inventory: "📋 库存同步",
  box_quantity: "📦 箱规同步",
  purchase_orders: "🚚 采购待到货",
  sales_statistics: "📊 销售统计",
  fx_rate: "💱 汇率",
  profit: "💰 利润回填",
  acos: "🎯 ACOS回填",
  monthly_lingxing: "📅 领星月度回填",
  daily_sales: "📈 逐日销量",
  profit_report: "💹 经营利润报表",
  purchase_sources: "🚚 采购计划/采购单",
  base_analysis: "🧪 基础数据分析",
};

const SYNC_TYPES = [
  "product",
  "sales",
  "inventory",
  "box_quantity",
  "purchase_orders",
  "sales_statistics",
  "fx_rate",
  "profit",
  "acos",
  "monthly_lingxing",
  "daily_sales",
  "profit_report",
  "purchase_sources",
  "base_analysis",
];

function fmtTime(iso?: string | null) {
  if (!iso) return "-";
  return iso.replace("T", " ").slice(0, 19);
}

export default function SyncLogsPage() {
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<string | null>(null);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.syncLogs
      .list()
      .then(setLogs)
      .catch((e: Error) => setMessage({ ok: false, text: e.message }))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  const trigger = async (syncType: string) => {
    setRunning(syncType);
    setMessage(null);
    try {
      const res = await api.syncLogs.run(syncType);
      setMessage({ ok: true, text: res.message });
      // 后台任务执行中，轮询日志直到没有 running 状态
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = setInterval(() => {
        api.syncLogs
          .list()
          .then(list => {
            setLogs(list);
            if (!list.some(l => l.status === "running")) {
              if (pollTimer.current) clearInterval(pollTimer.current);
              setRunning(null);
            }
          })
          .catch(() => { /* ignore */ });
      }, 5000);
      // 兜底：最多轮询 3 分钟
      setTimeout(() => {
        if (pollTimer.current) clearInterval(pollTimer.current);
        setRunning(null);
      }, 180000);
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "触发失败" });
      setRunning(null);
    }
  };

  const statusStyle = (status: string) => {
    if (status === "success") return { backgroundColor: "#dcfce7", color: "#166534" };
    if (status === "failed") return { backgroundColor: "#fee2e2", color: "#dc2626" };
    return { backgroundColor: "#dbeafe", color: "#1e40af" };
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <h1 className="text-2xl font-bold">同步日志</h1>
        <button
          onClick={load}
          className="px-3 py-1.5 rounded-md text-sm font-medium"
          style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
        >
          刷新
        </button>
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        每天 07:00-08:20 自动同步主数据（产品/销量/库存/待到货量/箱规/销售统计）；08:22-08:40 补充基础数据（月度/利润/ACOS/逐日销量/利润报表/采购来源）；08:45 基础分析。也可点击下方按钮立即同步。
      </p>

      <div className="card mb-6">
        <h2 className="text-sm font-semibold mb-3">立即同步</h2>
        <div className="flex flex-wrap gap-2">
          {SYNC_TYPES.map(t => (
            <button
              key={t}
              onClick={() => trigger(t)}
              disabled={running !== null}
              className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-blue)" }}
            >
              {running === t ? "同步中..." : TYPE_LABEL[t]}
            </button>
          ))}
        </div>
        {running && <p className="text-xs mt-2" style={{ color: "var(--accent-blue)" }}>后台同步进行中，完成后自动刷新日志…</p>}
        {message && (
          <p className="text-xs mt-2" style={{ color: message.ok ? "var(--accent-green)" : "var(--accent-red)" }}>{message.text}</p>
        )}
      </div>

      {loading && <p>加载中...</p>}
      {!loading && logs.length === 0 && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>
          暂无同步记录：API 服务需在 07:00 前后保持运行才会产生定时同步日志，或点击上方按钮立即同步。
        </div>
      )}
      {logs.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">类型</th><th className="text-center py-2 pr-3">状态</th>
              <th className="text-right py-2 pr-3">总记录</th><th className="text-right py-2 pr-3">成功</th>
              <th className="text-left py-2 pr-3">开始时间</th><th className="text-left py-2">完成时间</th>
            </tr></thead>
            <tbody>{logs.map(log => (
              <tr key={log.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                <td className="py-2 pr-3">{TYPE_LABEL[log.sync_type] || log.sync_type}</td>
                <td className="py-2 pr-3 text-center">
                  <span className="px-2 py-0.5 rounded text-xs font-medium" style={statusStyle(log.status)}>
                    {{ running: "运行中", success: "成功", failed: "失败" }[log.status] || log.status}
                  </span>
                </td>
                <td className="py-2 pr-3 text-right">{log.total_count ?? "-"}</td>
                <td className="py-2 pr-3 text-right">{log.success_count ?? "-"}</td>
                <td className="py-2 pr-3 font-mono text-xs">{fmtTime(log.started_at)}</td>
                <td className="py-2 font-mono text-xs">{fmtTime(log.completed_at)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {logs.some(l => l.error_message) && (
        <div className="card mt-4">
          <h2 className="text-lg font-semibold mb-3">错误详情</h2>
          {logs.filter(l => l.error_message).map(l => (
            <p key={l.id} className="text-xs mb-2 whitespace-pre-wrap" style={{ color: "var(--accent-red)" }}>
              [{fmtTime(l.started_at)}] {l.error_message}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
