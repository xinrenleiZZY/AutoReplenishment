"use client";

import { useEffect, useMemo, useState } from "react";
import { api, type CategoryLeadtime } from "@/lib/api";

interface FormState {
  level1_category: string;
  level2_category: string;
  lead_time_min: string;
  lead_time_max: string;
  notes: string;
}

const EMPTY_FORM: FormState = { level1_category: "", level2_category: "", lead_time_min: "", lead_time_max: "", notes: "" };

export default function CategoryLeadtimesPage() {
  const [items, setItems] = useState<CategoryLeadtime[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => {
    api.categoryLeadtimes
      .list()
      .then(setItems)
      .catch(() => setItems([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const groups = useMemo(() => {
    const map = new Map<string, CategoryLeadtime[]>();
    for (const item of items) {
      const key = item.level1_category || "未分类";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(item);
    }
    return [...map.entries()];
  }, [items]);

  const fmtLead = (item: CategoryLeadtime) => {
    if (item.lead_time_min == null && item.lead_time_max == null) return "未设置";
    if (item.lead_time_min != null && item.lead_time_max != null && item.lead_time_min === item.lead_time_max) {
      return `${item.lead_time_min} 天`;
    }
    return `${item.lead_time_min ?? "-"}-${item.lead_time_max ?? "-"} 天`;
  };

  const startEdit = (item: CategoryLeadtime) => {
    setEditingId(item.id);
    setForm({
      level1_category: item.level1_category ?? "",
      level2_category: item.level2_category ?? "",
      lead_time_min: item.lead_time_min != null ? String(item.lead_time_min) : "",
      lead_time_max: item.lead_time_max != null ? String(item.lead_time_max) : "",
      notes: item.notes ?? "",
    });
    setMsg(null);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setMsg(null);
  };

  const submit = async () => {
    if (!form.level1_category.trim()) {
      setMsg("一级分类不能为空");
      return;
    }
    setSaving(true);
    setMsg(null);
    const data = {
      level1_category: form.level1_category.trim(),
      level2_category: form.level2_category.trim() || undefined,
      lead_time_min: form.lead_time_min ? Number(form.lead_time_min) : undefined,
      lead_time_max: form.lead_time_max ? Number(form.lead_time_max) : undefined,
      notes: form.notes.trim() || undefined,
    };
    try {
      if (editingId != null) {
        await api.categoryLeadtimes.update(editingId, data);
        setMsg("已更新");
      } else {
        await api.categoryLeadtimes.create(data);
        setMsg("已新增");
      }
      cancelEdit();
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const remove = async (item: CategoryLeadtime) => {
    if (!window.confirm(`确认删除「${item.level1_category}${item.level2_category ? " / " + item.level2_category : ""}」？`)) return;
    try {
      await api.categoryLeadtimes.remove(item.id);
      setMsg("已删除");
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "删除失败");
    }
  };

  const inputCls = "mt-0.5 w-full px-2 py-1 rounded border text-xs";
  const inputStyle = { backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" } as const;

  const FormCard = ({ title }: { title: string }) => (
    <div className="card mb-4">
      <h3 className="text-sm font-semibold mb-3">{title}</h3>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
        <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          一级分类 *
          <input className={inputCls} style={inputStyle} value={form.level1_category}
            onChange={e => setForm(f => ({ ...f, level1_category: e.target.value }))} placeholder="如：毛毡类" />
        </label>
        <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          二级分类
          <input className={inputCls} style={inputStyle} value={form.level2_category}
            onChange={e => setForm(f => ({ ...f, level2_category: e.target.value }))} placeholder="可选" />
        </label>
        <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          工期最小（天）
          <input type="number" className={inputCls} style={inputStyle} value={form.lead_time_min}
            onChange={e => setForm(f => ({ ...f, lead_time_min: e.target.value }))} />
        </label>
        <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          工期最大（天）
          <input type="number" className={inputCls} style={inputStyle} value={form.lead_time_max}
            onChange={e => setForm(f => ({ ...f, lead_time_max: e.target.value }))} />
        </label>
        <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
          备注
          <input className={inputCls} style={inputStyle} value={form.notes}
            onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} />
        </label>
      </div>
      <div className="flex items-center gap-2">
        <button onClick={submit} disabled={saving}
          className="px-3 py-1.5 rounded-md text-xs font-medium text-white"
          style={{ backgroundColor: saving ? "#94a3b8" : "var(--accent-green)" }}>
          {saving ? "保存中..." : editingId != null ? "保存修改" : "新增"}
        </button>
        {editingId != null && (
          <button onClick={cancelEdit} className="px-3 py-1.5 rounded-md text-xs font-medium"
            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>取消</button>
        )}
        {msg && <span className="text-xs" style={{ color: msg.includes("失败") || msg.includes("不能为空") ? "var(--accent-red)" : "var(--accent-green)" }}>{msg}</span>}
      </div>
    </div>
  );

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">产品分类工期</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        {loading ? "加载中..." : `共 ${items.length} 条分类工期记录（可新增/编辑/删除，某些分类暂无法自动获取时手工维护）`}
      </p>
      <FormCard title={editingId != null ? "编辑分类工期" : "新增分类工期"} />
      {!loading && items.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无分类工期数据</div>}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {groups.map(([category, list]) => (
          <div key={category} className="card">
            <h3 className="font-semibold text-sm mb-2">{category}</h3>
            <ul className="space-y-1.5">
              {list.map(item => (
                <li key={item.id} className="text-xs flex justify-between items-center gap-2" style={{ color: "var(--text-secondary)" }}>
                  <span className="flex-1 truncate">{item.level2_category || "（未细分）"}</span>
                  <span className="font-mono">{fmtLead(item)}</span>
                  <span className="flex gap-1 shrink-0">
                    <button onClick={() => startEdit(item)} className="px-1.5 py-0.5 rounded text-[11px] font-medium"
                      style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--accent-blue)" }}>编辑</button>
                    <button onClick={() => remove(item)} className="px-1.5 py-0.5 rounded text-[11px] font-medium"
                      style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--accent-red)" }}>删除</button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
