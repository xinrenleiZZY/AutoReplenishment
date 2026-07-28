"use client";

export default function TimeAxisPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">销售时间轴</h1>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">销售阶段</h2>
          <div className="space-y-3">
            {[
              { phase: "备货期", desc: "距节日60-120天，海运备货", color: "#dbeafe" },
              { phase: "增长期", desc: "距节日30-60天，销量上升", color: "#dcfce7" },
              { phase: "峰值", desc: "距节日≤30天且在热卖期内", color: "#fef3c7" },
              { phase: "下降", desc: "节日已过30天内", color: "#ffe4e6" },
              { phase: "禁止采购", desc: "节日已过/远未到窗口", color: "#fecaca" },
              { phase: "正常销售", desc: "非节日产品无限制", color: "#f1f5f9" },
            ].map(item => (
              <div key={item.phase} className="flex items-center gap-3 p-2 rounded-md" style={{ backgroundColor: item.color }}>
                <span className="text-sm font-medium w-20">{item.phase}</span>
                <span className="text-xs" style={{ color: "var(--text-secondary)" }}>{item.desc}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">运输方式</h2>
          <div className="space-y-4">
            {[
              { mode: "🚢 海运", slow: "30天", peak: "45天", score: 100 },
              { mode: "✈️ 空派", slow: "10天", peak: "15天", score: 80 },
              { mode: "📦 快递", slow: "3天", peak: "6天", score: 60 },
            ].map(item => (
              <div key={item.mode} className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                <h3 className="font-medium text-sm mb-1">{item.mode}</h3>
                <div className="flex gap-4 text-xs" style={{ color: "var(--text-tertiary)" }}>
                  <span>淡季：{item.slow}</span><span>旺季：{item.peak}</span><span>评分：{item.score}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
