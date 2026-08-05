"use client";

import { useEffect, useState } from "react";
import { api, type FestivalCalendar } from "@/lib/api";

function fmtDate(dt?: string | null) {
  if (!dt) return "-";
  return dt.slice(0, 10);
}

export default function FestivalCalendarPage() {
  const [festivals, setFestivals] = useState<FestivalCalendar[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.festivalCalendar
      .list()
      .then(setFestivals)
      .catch(() => setFestivals([]))
      .finally(() => setLoading(false));
  }, []);

  const now = new Date().getMonth() + 1;
  const inSeason = (f: FestivalCalendar): boolean | null => {
    const s = f.hot_start_month;
    const e = f.hot_end_month;
    if (s == null || e == null) return null;
    return s <= e ? now >= s && now <= e : now >= s || now <= e;
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">节日日历</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>
        {loading ? "加载中..." : `共 ${festivals.length} 条节日记录`}
      </p>
      {!loading && festivals.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无节日数据</div>}
      {festivals.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-4">节日</th><th className="text-left py-2 pr-4">日期</th>
              <th className="text-left py-2 pr-4">热卖期</th><th className="text-center py-2 pr-4">热卖开始</th>
              <th className="text-center py-2 pr-4">热卖结束</th><th className="text-center py-2">状态</th>
            </tr></thead>
            <tbody>{festivals.map(f => {
              const season = inSeason(f);
              return (
                <tr key={f.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td className="py-2 pr-4">{f.festival}</td>
                  <td className="py-2 pr-4 font-mono text-xs">{fmtDate(f.festival_date)}</td>
                  <td className="py-2 pr-4 text-xs">{f.hot_period || "-"}</td>
                  <td className="py-2 pr-4 text-center">{f.hot_start_month != null ? `${f.hot_start_month}月` : "-"}</td>
                  <td className="py-2 pr-4 text-center">{f.hot_end_month != null ? `${f.hot_end_month}月` : "-"}</td>
                  <td className="py-2 text-center">
                    {season === true && <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "#dcfce7", color: "#166534" }}>🔥 热卖中</span>}
                    {season === false && <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "#f1f5f9", color: "#64748b" }}>非热卖期</span>}
                    {season === null && "-"}
                  </td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
