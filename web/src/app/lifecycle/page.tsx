"use client";

export default function LifecyclePage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">生命周期分析</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>产品在不同生命周期阶段匹配不同的采购策略。</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
        {[
          { phase: "🚀 启动期", score: 60, strategy: "小批测试", desc: "新品默认，上架≤1年", color: "#dbeafe" },
          { phase: "📈 增长期", score: 90, strategy: "逐步增加采购", desc: "销量增长率≥50%", color: "#dcfce7" },
          { phase: "🔥 热卖期", score: 100, strategy: "保证不断货", desc: "增长率≥20%或≥50%且销量>100", color: "#fef3c7" },
          { phase: "✅ 成熟期", score: 70, strategy: "稳定补货", desc: "增长率±20%", color: "#f1f5f9" },
          { phase: "📉 下降期", score: 30, strategy: "减少采购", desc: "销量下降20%-50%", color: "#ffe4e6" },
          { phase: "⏹ 清库存期", score: 0, strategy: "停止采购", desc: "销量下降超50%", color: "#fecaca" },
        ].map(item => (
          <div key={item.phase} className="card" style={{ borderLeft: `4px solid ${item.color}` }}>
            <h3 className="font-semibold text-sm mb-1">{item.phase}</h3>
            <p className="text-xs mb-2" style={{ color: "var(--text-tertiary)" }}>{item.desc}</p>
            <div className="flex justify-between text-xs"><span>策略：<strong>{item.strategy}</strong></span><span>评分：<strong>{item.score}</strong></span></div>
          </div>
        ))}
      </div>
      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-3">等级</h2>
        <div className="grid grid-cols-5 gap-3 text-sm text-center">
          {[{ lv: "S", sales: "≥5000" }, { lv: "A", sales: "2000-4999" }, { lv: "B", sales: "1000-1999" }, { lv: "C", sales: "300-999" }, { lv: "D", sales: "1-299" }].map(item => (
            <div key={item.lv} className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}><p className="text-lg font-bold">{item.lv}</p><p className="text-xs mt-1">{item.sales}</p></div>
          ))}
        </div>
      </div>
    </div>
  );
}
