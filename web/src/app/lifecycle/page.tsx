"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type FestivalCalendar,
  type FestivalCalendarPayload,
  type LifecycleApplyResult,
  type LifecycleDistribution,
  type LifecycleStats,
} from "@/lib/api";

const PHASES = [
  { phase: "启动期", strategy: "小批测试", color: "#dbeafe" },
  { phase: "增长期", strategy: "逐步增加采购", color: "#dcfce7" },
  { phase: "热卖期", strategy: "保证不断货", color: "#fef3c7" },
  { phase: "成熟期", strategy: "稳定补货", color: "#f1f5f9" },
  { phase: "下降期", strategy: "减少采购/不采购", color: "#ffe4e6" },
];

const LEVELS = [
  { lv: "S", sales: "≥5000" },
  { lv: "A", sales: "2000-4999" },
  { lv: "B", sales: "1000-1999" },
  { lv: "C", sales: "300-999" },
  { lv: "D", sales: "1-299" },
];

const EMPTY_FORM: FestivalCalendarPayload = {
  festival: "",
  festival_en: "",
  listing_start: "",
  festival_date: "",
  festival_end: "",
  hot_period: "",
  hot_start_month: null,
  hot_end_month: null,
  launch_stage: "",
  growth_stage: "",
  mature_stage: "",
  decline_stage: "",
  notes: "",
};

function fmtDate(v?: string | null) {
  return v ? v.slice(0, 10) : "-";
}

export default function LifecyclePage() {
  const [stats, setStats] = useState<LifecycleStats | null>(null);
  const [dist, setDist] = useState<LifecycleDistribution | null>(null);
  const [festivals, setFestivals] = useState<FestivalCalendar[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ type: "ok" | "err"; text: string } | null>(null);
  const [editing, setEditing] = useState<FestivalCalendar | null>(null);
  const [form, setForm] = useState<FestivalCalendarPayload>(EMPTY_FORM);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const [s, d, f] = await Promise.all([
        api.products.lifecycleStats().catch(() => null),
        api.festivalCalendar.lifecycleDistribution("S,A").catch(() => null),
        api.festivalCalendar.list().catch(() => []),
      ]);
      setStats(s);
      setDist(d);
      setFestivals(f);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const flash = (type: "ok" | "err", text: string) => {
    setMsg({ type, text });
    setTimeout(() => setMsg(null), 5000);
  };

  const openAdd = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
  };

  const openEdit = (f: FestivalCalendar) => {
    setEditing(f);
    setForm({
      festival: f.festival,
      festival_en: f.festival_en ?? "",
      listing_start: f.listing_start ?? "",
      festival_date: f.festival_date ?? "",
      festival_end: f.festival_end ?? "",
      hot_period: f.hot_period ?? "",
      hot_start_month: f.hot_start_month,
      hot_end_month: f.hot_end_month,
      launch_stage: f.launch_stage ?? "",
      growth_stage: f.growth_stage ?? "",
      mature_stage: f.mature_stage ?? "",
      decline_stage: f.decline_stage ?? "",
      notes: f.notes ?? "",
    });
  };

  const save = async () => {
    if (!form.festival?.trim()) {
      flash("err", "节日/主题 必填");
      return;
    }
    setBusy(true);
    try {
      const payload: FestivalCalendarPayload = { ...form, festival: form.festival.trim() };
      if (editing) {
        await api.festivalCalendar.update(editing.id, payload);
        flash("ok", "已更新");
      } else {
        await api.festivalCalendar.create(payload);
        flash("ok", "已新增");
      }
      setEditing(null);
      setForm(EMPTY_FORM);
      await load();
    } catch (e) {
      flash("err", String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (f: FestivalCalendar) => {
    if (!window.confirm(`确定删除「${f.festival}」这条时间点记录？`)) return;
    setBusy(true);
    try {
      await api.festivalCalendar.remove(f.id);
      flash("ok", "已删除");
      await load();
    } catch (e) {
      flash("err", String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const onImport = async (file: File) => {
    setBusy(true);
    try {
      const r = await api.festivalCalendar.importFile(file);
      flash("ok", `导入成功：${r.imported} 条（${r.festivals.length} 个节日）`);
      await load();
    } catch (e) {
      flash("err", String((e as Error).message || e));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const applyLifecycle = async () => {
    if (!window.confirm("按「节日时间点表 + 当前时间」重算 S/A 生命周期并写库？（无节日产品跳过，保持原值）")) return;
    setBusy(true);
    try {
      const r: LifecycleApplyResult = await api.festivalCalendar.applyLifecycle("S,A");
      flash("ok", `重算完成：匹配 ${r.matched} / 更新 ${r.updated} / 未匹配 ${r.fallback}`);
      await load();
    } catch (e) {
      flash("err", String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const countOf = (map: Record<string, number> | undefined, label: string) => map?.[label] ?? 0;
  const arrCountOf = (list: { label: string; count: number }[] | undefined, label: string) =>
    list?.find(x => x.label === label)?.count ?? 0;
  const total = dist?.total ?? 0;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">生命周期分析</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>
        生命周期按「节日时间点表（节日 + 当前时间）」判定；无节日/匹配不到保持原值。
        当前统计（S/A 级）：{loading ? "加载中..." : `${total} 个产品，其中 ${dist?.fallback ?? 0} 个未匹配`}
      </p>

      {msg && (
        <div className={`card px-4 py-2 mb-4 text-sm ${msg.type === "ok" ? "" : ""}`}
          style={{ backgroundColor: msg.type === "ok" ? "#dcfce7" : "#ffe4e6", color: msg.type === "ok" ? "#166534" : "#991b1b" }}>
          {msg.text}
        </div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        {PHASES.map(item => (
          <div key={item.phase} className="card" style={{ borderLeft: `4px solid ${item.color}` }}>
            <div className="flex items-center justify-between mb-1">
              <h3 className="font-semibold text-sm">{item.phase}</h3>
              <span className="text-xs px-2 py-0.5 rounded" style={{ backgroundColor: "var(--bg-tertiary)" }}>
                {loading ? "-" : countOf(dist?.by_lifecycle, item.phase)} 个
              </span>
            </div>
            <p className="text-xs mt-2" style={{ color: "var(--text-tertiary)" }}>策略：{item.strategy}</p>
          </div>
        ))}
      </div>

      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-3">等级分布（全量启用产品）</h2>
        <div className="grid grid-cols-5 gap-3 text-sm text-center">
          {LEVELS.map(item => (
            <div key={item.lv} className="p-3 rounded-md" style={{ backgroundColor: "var(--bg-tertiary)" }}>
              <p className="text-lg font-bold">{item.lv}</p>
              <p className="text-xs mt-1">{item.sales}</p>
              <p className="text-xs mt-1 font-medium">{loading ? "-" : arrCountOf(stats?.by_product_level, item.lv)} 个</p>
            </div>
          ))}
        </div>
      </div>

      <div className="card mb-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <h2 className="text-lg font-semibold">节日生命周期时间点（festival_calendar）</h2>
          <div className="flex flex-wrap gap-2">
            <button onClick={openAdd} className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: "var(--accent-blue)" }}>＋ 新增</button>
            <button onClick={() => api.festivalCalendar.downloadTemplate().catch(e => flash("err", String((e as Error).message || e)))}
              className="px-3 py-1.5 rounded-md text-sm font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>下载模板</button>
            <button onClick={() => fileRef.current?.click()} className="px-3 py-1.5 rounded-md text-sm font-medium"
              style={{ backgroundColor: "var(--bg-tertiary)" }}>导入表格</button>
            <input ref={fileRef} type="file" accept=".xlsx" className="hidden"
              onChange={e => e.target.files?.[0] && onImport(e.target.files[0])} />
            <button onClick={applyLifecycle} disabled={busy} className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: busy ? "#94a3b8" : "var(--accent-green, #16a34a)" }}>
              {busy ? "处理中..." : "按节日时间点重算 S/A 生命周期"}
            </button>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">节日/主题</th>
              <th className="text-left py-2 pr-3">上架开售</th>
              <th className="text-left py-2 pr-3">节日时间</th>
              <th className="text-left py-2 pr-3">结束时间</th>
              <th className="text-center py-2 pr-3">启动期</th>
              <th className="text-center py-2 pr-3">增长期</th>
              <th className="text-center py-2 pr-3">热卖期</th>
              <th className="text-center py-2 pr-3">成熟期</th>
              <th className="text-center py-2 pr-3">下降期</th>
              <th className="text-center py-2">操作</th>
            </tr></thead>
            <tbody>
              {festivals.map(f => (
                <tr key={f.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td className="py-2 pr-3 font-medium">{f.festival}</td>
                  <td className="py-2 pr-3 text-xs font-mono">{fmtDate(f.listing_start)}</td>
                  <td className="py-2 pr-3 text-xs font-mono">{fmtDate(f.festival_date)}</td>
                  <td className="py-2 pr-3 text-xs font-mono">{fmtDate(f.festival_end)}</td>
                  <td className="py-2 pr-3 text-center text-xs">{f.launch_stage || "-"}</td>
                  <td className="py-2 pr-3 text-center text-xs">{f.growth_stage || "-"}</td>
                  <td className="py-2 pr-3 text-center text-xs">{f.hot_period || "-"}</td>
                  <td className="py-2 pr-3 text-center text-xs">{f.mature_stage || "-"}</td>
                  <td className="py-2 pr-3 text-center text-xs">{f.decline_stage || "-"}</td>
                  <td className="py-2 text-center whitespace-nowrap">
                    <button onClick={() => openEdit(f)} className="px-2 py-1 rounded text-xs font-medium mr-1"
                      style={{ backgroundColor: "var(--bg-tertiary)" }}>编辑</button>
                    <button onClick={() => remove(f)} className="px-2 py-1 rounded text-xs font-medium"
                      style={{ backgroundColor: "#ffe4e6", color: "#991b1b" }}>删除</button>
                  </td>
                </tr>
              ))}
              {!loading && festivals.length === 0 && (
                <tr><td colSpan={10} className="py-8 text-center" style={{ color: "var(--text-tertiary)" }}>暂无节日时间点数据</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {(editing || form.festival) && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(15,23,42,0.5)" }}>
          <div className="card w-full max-w-3xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold mb-4">{editing ? `编辑：${editing.festival}` : "新增节日时间点"}</h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <label className="col-span-2">节日/主题 *
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.festival} onChange={e => setForm({ ...form, festival: e.target.value })} /></label>
              <label>亚马逊预计上架开售时间
                <input type="date" className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.listing_start ? form.listing_start.slice(0, 10) : ""}
                  onChange={e => setForm({ ...form, listing_start: e.target.value })} /></label>
              <label>节日/主题时间
                <input type="date" className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.festival_date ? form.festival_date.slice(0, 10) : ""}
                  onChange={e => setForm({ ...form, festival_date: e.target.value })} /></label>
              <label>预计节日/主题结束时间
                <input type="date" className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.festival_end ? form.festival_end.slice(0, 10) : ""}
                  onChange={e => setForm({ ...form, festival_end: e.target.value })} /></label>
              <label>启动期（如 7月）
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.launch_stage ?? ""} onChange={e => setForm({ ...form, launch_stage: e.target.value })} /></label>
              <label>增长期（如 8月-9月上旬）
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.growth_stage ?? ""} onChange={e => setForm({ ...form, growth_stage: e.target.value })} /></label>
              <label>热卖期（如 9月中旬-10月上旬）
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.hot_period ?? ""} onChange={e => setForm({ ...form, hot_period: e.target.value })} /></label>
              <label>成熟期（如 10月中旬）
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.mature_stage ?? ""} onChange={e => setForm({ ...form, mature_stage: e.target.value })} /></label>
              <label>下降期（如 10月下旬）
                <input className="w-full mt-1 p-2 rounded-md border" style={{ borderColor: "var(--border-color)" }}
                  value={form.decline_stage ?? ""} onChange={e => setForm({ ...form, decline_stage: e.target.value })} /></label>
              <label className="col-span-2">备注
                <textarea className="w-full mt-1 p-2 rounded-md border" rows={2} style={{ borderColor: "var(--border-color)" }}
                  value={form.notes ?? ""} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => { setEditing(null); setForm(EMPTY_FORM); }}
                className="px-3 py-1.5 rounded-md text-sm font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>取消</button>
              <button onClick={save} disabled={busy} className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: busy ? "#94a3b8" : "var(--accent-blue)" }}>{busy ? "保存中..." : "保存"}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
