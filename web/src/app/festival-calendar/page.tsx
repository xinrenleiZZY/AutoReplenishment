"use client";

export default function FestivalCalendarPage() {
  const festivals = [
    { festival: "新年", date: "2026-01-01", hot: "11-12月", start: 11, end: 12 },
    { festival: "情人节", date: "2026-02-14", hot: "12-2月", start: 12, end: 2 },
    { festival: "复活节", date: "2026-04-05", hot: "1-4月", start: 1, end: 4 },
    { festival: "母亲节", date: "2026-05-10", hot: "3-5月", start: 3, end: 5 },
    { festival: "父亲节", date: "2026-06-21", hot: "4-6月", start: 4, end: 6 },
    { festival: "国庆节", date: "2026-07-04", hot: "3-7月", start: 3, end: 7 },
    { festival: "万圣节", date: "2026-10-31", hot: "7-10月", start: 7, end: 10 },
    { festival: "感恩节", date: "2026-11-26", hot: "7-11月", start: 7, end: 11 },
    { festival: "圣诞节", date: "2026-12-25", hot: "8-12月", start: 8, end: 12 },
    { festival: "返校季", date: "7-9月", hot: "6-9月", start: 6, end: 9 },
    { festival: "春季", date: "2-4月", hot: "2-4月", start: 2, end: 4 },
    { festival: "夏季", date: "3-7月", hot: "3-7月", start: 3, end: 7 },
    { festival: "秋季", date: "8-11月", hot: "8-11月", start: 8, end: 11 },
    { festival: "冬季", date: "10-1月", hot: "10-1月", start: 10, end: 1 },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">节日日历</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>49条节日记录（来自种子数据）</p>
      <div className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
            <th className="text-left py-2 pr-4">节日</th><th className="text-left py-2 pr-4">日期</th>
            <th className="text-center py-2 pr-4">热卖开始</th><th className="text-center py-2 pr-4">热卖结束</th>
            <th className="text-center py-2">状态</th>
          </tr></thead>
          <tbody>{festivals.map((f, i) => {
            const now = new Date().getMonth() + 1;
            const inSeason = f.start <= f.end ? (now >= f.start && now <= f.end) : (now >= f.start || now <= f.end);
            return (
              <tr key={i} style={{ borderBottom: "1px solid var(--border-color)" }}>
                <td className="py-2 pr-4">{f.festival}</td>
                <td className="py-2 pr-4 font-mono text-xs">{f.date}</td>
                <td className="py-2 pr-4 text-center">{f.start}月</td>
                <td className="py-2 pr-4 text-center">{f.end}月</td>
                <td className="py-2 text-center">{inSeason ? <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "#dcfce7", color: "#166534" }}>🔥 热卖中</span> : "-"}</td>
              </tr>
            );
          })}</tbody>
        </table>
      </div>
    </div>
  );
}
