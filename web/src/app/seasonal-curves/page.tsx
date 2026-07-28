"use client";

export default function SeasonalCurvesPage() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">季节销售曲线</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>用于新品销量预测的销售占比曲线模板。</p>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-3">🎄 圣诞装饰品</h2>
          <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>售卖截止：节前12天</p>
          {[["8月", 3], ["9月", 12], ["10月", 30], ["11月", 48], ["12月", 7]].map(([m, p]) => (
            <div key={m} className="flex items-center gap-2 text-sm mb-1.5">
              <span className="w-8">{m}</span>
              <div className="flex-1 h-4 rounded" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                <div className="h-4 rounded" style={{ width: `${p}%`, backgroundColor: "var(--accent-blue)" }} />
              </div>
              <span className="w-12 text-right font-mono">{p}%</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2 className="text-lg font-semibold mb-3">🎄 圣诞非装饰品</h2>
          <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>售卖截止：节前3天</p>
          {[["8月", 3], ["9月", 6], ["10月", 20], ["11月", 55], ["12月", 16]].map(([m, p]) => (
            <div key={m} className="flex items-center gap-2 text-sm mb-1.5">
              <span className="w-8">{m}</span>
              <div className="flex-1 h-4 rounded" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                <div className="h-4 rounded" style={{ width: `${p}%`, backgroundColor: "var(--accent-green)" }} />
              </div>
              <span className="w-12 text-right font-mono">{p}%</span>
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2 className="text-lg font-semibold mb-3">构建说明</h2>
        <ul className="text-sm space-y-1" style={{ color: "var(--text-secondary)" }}>
          <li>• 筛选：同节日、同类型、同价格区间±20%、≥4个成熟产品</li>
          <li>• 系统自动计算各月占比平均值</li>
          <li>• 需导入销量数据后自动构建</li>
        </ul>
      </div>
    </div>
  );
}
