"use client";

import { useEffect, useState } from "react";
import { api, type SyncLog } from "@/lib/api";

const TYPE_LABEL: Record<string, string> = {
  product: "📦 产品同步",
  sales: "📈 销量同步",
  inventory: "📋 库存同步",
  box_quantity: "📦 箱规同步",
};

function fmtTime(iso?: string | null) {
  if (!iso) return "-";
  return iso.replace("T", " ").slice(0, 19);
}

export default function SyncLogsPage() {
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.syncLogs
      .list()
      .then(setLogs)
      .catch(() => setLogs([]))
      .finally(() => setLoading(false));
  }, []);

  const statusStyle = (status: string) => {
    if (status === "success") return { backgroundColor: "#dcfce7", color: "#166534" };
    if (status === "failed") return { backgroundColor: "#fee2e2", color: "#dc2626" };
    return { backgroundColor: "#dbeafe", color: "#1e40af" };
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">同步日志</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>领星数据同步记录</p>
      {loading && <p>加载中...</p>}
      {!loading && logs.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无同步记录</div>}
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
