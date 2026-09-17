"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type ConfigParamItem } from "@/lib/api";

export default function ParametersPage() {
  const [params, setParams] = useState<ConfigParamItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    api.config
      .list()
      .then(list => {
        setParams(list);
        setEditing(Object.fromEntries(list.map(p => [p.key, String(p.value)])));
      })
      .catch((e: Error) => setMessage({ ok: false, text: e.message }))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { load(); }, [load]);

  // 与数据来源核验一致的状态判定：编辑值 ≠ 服务端基准值即为「修改中」
  const isDirty = (p: ConfigParamItem) => (editing[p.key] ?? "") !== String(p.value);

  const feeKeys = new Set([
    "sea_slow_fee", "sea_peak_fee", "air_slow_fee", "air_peak_fee",
    "express_slow_fee", "express_peak_fee",
  ]);

  const save = async (key: string) => {
    try {
      const res = await api.config.update(key, editing[key] ?? "");
      setMessage({ ok: true, text: `${key} 已更新为 ${res.value}` });
      // 立即同步该键的基准值，状态列即刻回到「已保存」
      setEditing(prev => ({ ...prev, [key]: String(res.value) }));
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "保存失败" });
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">自定义参数</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        参数修改后<b style={{ color: "var(--text-primary)" }}>实时写入数据库，无需重启容器服务</b>；每次计算/同步时都会读取最新值，下次计算/同步即生效。
      </p>
      {message && (
        <div className="card mb-4 text-sm" style={{
          borderLeft: `4px solid ${message.ok ? "var(--accent-green)" : "var(--accent-red)"}`,
          color: message.ok ? "inherit" : "var(--accent-red)",
        }}>
          {message.text}
        </div>
      )}
      {loading && <p>加载中...</p>}
      {!loading && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-center py-2 pr-3">操作</th>
              <th className="text-left py-2 pr-3">状态</th>
              <th className="text-left py-2 pr-3">参数</th>
              <th className="text-left py-2 pr-3">说明</th>
              <th className="text-left py-2 pr-3">当前值</th>
              <th className="text-left py-2 pr-3">默认值</th>
            </tr></thead>
            <tbody>
              {params.map(p => {
                const dirty = isDirty(p);
                return (
                <tr key={p.key} style={{ borderBottom: "1px solid var(--border-color)", backgroundColor: dirty ? "rgba(46,204,113,0.08)" : "transparent" }}>
                  <td className="py-2 pr-3 text-center">
                    <button
                      onClick={() => save(p.key)}
                      className="px-3 py-1 rounded-md text-xs font-medium text-white"
                      style={{ backgroundColor: "var(--accent-green)" }}
                    >
                      保存
                    </button>
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    {dirty ? <span style={{ color: "var(--accent-green)" }}>● 修改中</span> : <span style={{ color: "var(--text-tertiary)" }}>已保存</span>}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    <div style={{ maxWidth: 400, whiteSpace: "normal", overflowWrap: "anywhere" }}>
                      {p.key}
                      {p.is_override && (
                        <span className="ml-1 px-1 py-0.5 rounded text-[10px]" style={{ backgroundColor: "#fef3c7", color: "#92400e" }}>已覆盖</span>
                      )}
                    </div>
                  </td>
                  <td className="py-2 pr-3 text-xs">
                    <div style={{ maxWidth: 400, whiteSpace: "normal", overflowWrap: "anywhere" }}>{p.description}</div>
                  </td>
                  <td className="py-2 pr-3">
                    <span className="inline-flex items-center gap-1">
                      {feeKeys.has(p.key) && <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>￥</span>}
                      <input
                        type="text"
                        value={editing[p.key] ?? ""}
                        onChange={e => setEditing(prev => ({ ...prev, [p.key]: e.target.value }))}
                        className="text-sm px-2 py-1 rounded border w-44"
                        style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                      />
                    </span>
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    <div style={{ maxWidth: 400, whiteSpace: "normal", overflowWrap: "anywhere" }}>
                      {feeKeys.has(p.key) ? `￥${String(p.default)}` : String(p.default)}
                    </div>
                  </td>
                </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
