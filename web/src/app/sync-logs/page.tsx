"use client";

export default function SyncLogsPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">同步日志</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>领星数据同步记录（需配置API密钥后自动执行）</p>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="p-4 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}>
          <h3 className="text-sm font-medium mb-2">📦 产品同步</h3>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>来源：领星API</p>
          <p className="text-xs" style={{ color: "var(--accent-green)" }}>✅ 已导入 4,413 条</p>
        </div>
        <div className="p-4 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}>
          <h3 className="text-sm font-medium mb-2">📈 销量同步</h3>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>来源：卖家精灵MCP</p>
          <p className="text-xs" style={{ color: "var(--accent-orange)" }}>⏳ 待配置</p>
        </div>
        <div className="p-4 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}>
          <h3 className="text-sm font-medium mb-2">📋 库存同步</h3>
          <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>来源：领星API</p>
          <p className="text-xs" style={{ color: "var(--accent-orange)" }}>⏳ 待提取</p>
        </div>
      </div>
    </div>
  );
}
