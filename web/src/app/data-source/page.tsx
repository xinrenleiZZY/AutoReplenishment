"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { api, type DataSourceDict, type DataSourceScanResult } from "@/lib/api";

const SOURCE_OPTIONS = ["领星", "MCP", "本地计算", "AI", "系统", "人工", "未知"];
const METHOD_OPTIONS = ["网页API每日抓取", "API", "MCP", "每日抓取", "规则计算", "公式计算", "多因素加权", "月度回填", "回填", "人工维护", "未知"];
const FREQ_OPTIONS = ["每日", "每周", "月度", "实时", "手动", "按等级频率", "未知"];
const CATEGORY_OPTIONS = ["产品", "销量", "库存", "计算", "成本", "基础数据", "系统", "AI", "其他"];

interface CreateForm {
  table_name: string;
  field_name: string;
  field_comment: string;
  data_source: string;
  collect_method: string;
  update_freq: string;
  category: string;
  notes: string;
}

const EMPTY_CREATE: CreateForm = {
  table_name: "", field_name: "", field_comment: "",
  data_source: "", collect_method: "", update_freq: "", category: "", notes: "",
};

function cellInput(className = "w-full px-2 py-1 rounded border text-xs") {
  return { className, style: { backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" } as const };
}

export default function DataSourcePage() {
  const [items, setItems] = useState<DataSourceDict[]>([]);
  const [baseItems, setBaseItems] = useState<DataSourceDict[]>([]);
  const baseRef = useRef<DataSourceDict[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [category, setCategory] = useState("");
  const [q, setQ] = useState("");
  const [usageFilter, setUsageFilter] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [createForm, setCreateForm] = useState<CreateForm>(EMPTY_CREATE);

  const load = () => {
    api.dataSource
      .list({ limit: 2000 })
      .then((data) => {
        setItems(data);
        setBaseItems(data);
        baseRef.current = data;
      })
      .catch((e) => setMsg(e instanceof Error ? e.message : "加载失败"))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const updateRow = (id: number, patch: Partial<DataSourceDict>) => {
    setItems((prev) => prev.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  };

  const isDirty = (r: DataSourceDict) => {
    const b = baseRef.current.find((x) => x.id === r.id);
    if (!b) return false;
    return (
      b.field_comment !== r.field_comment ||
      b.data_source !== r.data_source ||
      b.collect_method !== r.collect_method ||
      b.update_freq !== r.update_freq ||
      b.notes !== r.notes ||
      b.category !== r.category ||
      b.sort_order !== r.sort_order
    );
  };

  const changedRows = useMemo(
    () => items.filter(isDirty),
    [items]
  );

  const visibleItems = useMemo(() => {
    let list = items;
    if (category) list = list.filter((r) => r.category === category);
    if (q.trim()) {
      const kw = q.trim().toLowerCase();
      list = list.filter(
        (r) =>
          r.table_name.toLowerCase().includes(kw) ||
          r.field_name.toLowerCase().includes(kw) ||
          (r.field_comment ?? "").toLowerCase().includes(kw)
      );
    }
    if (usageFilter === "used") list = list.filter((r) => r.is_used);
    else if (usageFilter === "unused") list = list.filter((r) => r.is_used === false);
    return list;
  }, [items, category, q, usageFilter]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    for (const r of items) if (r.category) set.add(r.category);
    return [...set].sort();
  }, [items]);

  const usedCount = useMemo(() => visibleItems.filter((r) => r.is_used).length, [visibleItems]);
  const unusedCount = visibleItems.length - usedCount;

  const handleScan = async () => {
    if (!window.confirm("将重新扫描所有数据表字段，生成/补全数据来源字典，会保留你已经编辑的内容。确定继续？")) return;
    setScanning(true);
    setMsg(null);
    try {
      const res: DataSourceScanResult = await api.dataSource.scan();
      const total = Math.max(res.added, 0) + Math.max(res.updated, 0);
      setMsg(`扫描完成：新增 ${res.added} 条，补全 ${res.updated} 条。`);
      load();
      void total;
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "扫描失败");
    } finally {
      setScanning(false);
    }
  };

  const handleSave = async () => {
    if (changedRows.length === 0) {
      setMsg("没有需要保存的修改");
      return;
    }
    setSaving(true);
    setMsg(null);
    try {
      const payload = changedRows.map((r) => ({
        id: r.id,
        field_comment: r.field_comment,
        data_source: r.data_source,
        collect_method: r.collect_method,
        update_freq: r.update_freq,
        notes: r.notes,
        category: r.category,
        sort_order: r.sort_order,
      }));
      const saved = await api.dataSource.batchUpdate(payload);
      setMsg(`已保存 ${saved.length} 条修改`);
      // 以服务端返回的权威数据同步 items 与基准快照，状态列立即回到「已保存」，
      // 避免依赖网络回刷的时序导致「修改中」残留。
      const savedMap = new Map(saved.map((s) => [s.id, s]));
      setItems((prev) => {
        const next = prev.map((r) => savedMap.get(r.id) ?? r);
        baseRef.current = next;
        return next;
      });
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const handleRemove = async (r: DataSourceDict) => {
    if (!window.confirm(`确认删除「${r.table_name}.${r.field_name}」？`)) return;
    try {
      await api.dataSource.remove(r.id);
      setMsg("已删除");
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "删除失败");
    }
  };

  const handleCreate = async () => {
    if (!createForm.table_name.trim() || !createForm.field_name.trim()) {
      setMsg("表名与字段名不能为空");
      return;
    }
    try {
      await api.dataSource.create({
        table_name: createForm.table_name.trim(),
        field_name: createForm.field_name.trim(),
        field_comment: createForm.field_comment.trim() || null,
        data_source: createForm.data_source || null,
        collect_method: createForm.collect_method || null,
        update_freq: createForm.update_freq || null,
        category: createForm.category || null,
        notes: createForm.notes.trim() || null,
      });
      setMsg("已新增");
      setCreateForm(EMPTY_CREATE);
      setShowCreate(false);
      load();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "新增失败");
    }
  };

  const inputCls = "w-full px-2 py-1 rounded border text-xs";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">数据来源核验</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        逐字段列出 ASIN 相关所有数据字段与分析指标的数据来源、采集方式、更新频率，用于字段级数据溯源与核验。
      </p>

      {/* 工具栏 */}
      <div className="card mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            分组
            <select
              className={`${inputCls} w-28`}
              style={{ ...cellInput().style }}
              value={category}
              onChange={(e) => setCategory(e.target.value)}
            >
              <option value="">全部</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c}</option>
              ))}
            </select>
          </label>
          <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            使用情况
            <select
              className={`${inputCls} w-28`}
              style={{ ...cellInput().style }}
              value={usageFilter}
              onChange={(e) => setUsageFilter(e.target.value)}
            >
              <option value="">全部</option>
              <option value="used">已使用</option>
              <option value="unused">未使用</option>
            </select>
          </label>
          <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            搜索
            <input
              className={`${inputCls} w-52`}
              style={cellInput().style}
              placeholder="表名/字段名/说明"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </label>
          <div className="flex-1" />
          <button
            onClick={handleScan}
            disabled={scanning}
            className="px-3 py-1.5 rounded-md text-xs font-medium text-white"
            style={{ backgroundColor: scanning ? "#94a3b8" : "var(--accent-blue)" }}
          >
            {scanning ? "扫描中..." : "重扫生成"}
          </button>
          <button
            onClick={() => setShowCreate((v) => !v)}
            className="px-3 py-1.5 rounded-md text-xs font-medium"
            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
          >
            新增字段
          </button>
          <button
            onClick={handleSave}
            disabled={saving || changedRows.length === 0}
            className="px-3 py-1.5 rounded-md text-xs font-medium text-white"
            style={{ backgroundColor: saving || changedRows.length === 0 ? "#94a3b8" : "var(--accent-green)" }}
          >
            {saving ? "保存中..." : `保存修改 (${changedRows.length})`}
          </button>
        </div>
        {msg && (
          <p className="text-xs mt-2" style={{ color: msg.includes("失败") || msg.includes("不能为空") ? "var(--accent-red)" : "var(--accent-green)" }}>
            {msg}
          </p>
        )}
      </div>

      {/* 新增表单 */}
      {showCreate && (
        <div className="card mb-4">
          <h3 className="text-sm font-semibold mb-3">新增字段来源记录</h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-3">
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              所属表 *
              <input className={inputCls} style={cellInput().style} value={createForm.table_name}
                onChange={(e) => setCreateForm((f) => ({ ...f, table_name: e.target.value }))} placeholder="如：products" />
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              字段/指标名 *
              <input className={inputCls} style={cellInput().style} value={createForm.field_name}
                onChange={(e) => setCreateForm((f) => ({ ...f, field_name: e.target.value }))} placeholder="如：life_cycle" />
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              字段说明
              <input className={inputCls} style={cellInput().style} value={createForm.field_comment}
                onChange={(e) => setCreateForm((f) => ({ ...f, field_comment: e.target.value }))} />
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              分组
              <select className={inputCls} style={cellInput().style} value={createForm.category}
                onChange={(e) => setCreateForm((f) => ({ ...f, category: e.target.value }))}>
                <option value="">未选择</option>
                {CATEGORY_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              数据来源
              <select className={inputCls} style={cellInput().style} value={createForm.data_source}
                onChange={(e) => setCreateForm((f) => ({ ...f, data_source: e.target.value }))}>
                <option value="">未选择</option>
                {SOURCE_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              采集方式
              <select className={inputCls} style={cellInput().style} value={createForm.collect_method}
                onChange={(e) => setCreateForm((f) => ({ ...f, collect_method: e.target.value }))}>
                <option value="">未选择</option>
                {METHOD_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              更新频率
              <select className={inputCls} style={cellInput().style} value={createForm.update_freq}
                onChange={(e) => setCreateForm((f) => ({ ...f, update_freq: e.target.value }))}>
                <option value="">未选择</option>
                {FREQ_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label className="text-xs" style={{ color: "var(--text-tertiary)" }}>
              备注
              <input className={inputCls} style={cellInput().style} value={createForm.notes}
                onChange={(e) => setCreateForm((f) => ({ ...f, notes: e.target.value }))} />
            </label>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleCreate} className="px-3 py-1.5 rounded-md text-xs font-medium text-white"
              style={{ backgroundColor: "var(--accent-green)" }}>新增</button>
            <button onClick={() => setShowCreate(false)} className="px-3 py-1.5 rounded-md text-xs font-medium"
              style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>取消</button>
          </div>
        </div>
      )}

      {/* 结果表格 */}
      {loading && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>加载中...</div>}
      {!loading && visibleItems.length === 0 && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>
          暂无数据来源记录，点击右上角「重扫生成」自动扫描数据库字段。
        </div>
      )}
      {!loading && visibleItems.length > 0 && (
        <div className="card overflow-hidden">
          <p className="text-xs px-4 pt-3 pb-2" style={{ color: "var(--text-tertiary)" }}>
            共 {items.length} 条字段记录，当前显示 {visibleItems.length} 条；其中已使用 {usedCount} 条、未使用 {unusedCount} 条。绿色高亮且带"•"标记的行有未保存的修改。
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-xs" style={{ color: "var(--text-secondary)" }}>
              <thead>
                <tr className="text-left" style={{ borderBottom: "1px solid var(--border-color)", color: "var(--text-tertiary)" }}>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">状态</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">分组</th>
                  <th className="px-3 py-2 font-medium">所属表</th>
                  <th className="px-3 py-2 font-medium">字段</th>
                  <th className="px-3 py-2 font-medium">字段说明</th>
                  <th className="px-3 py-2 font-medium">数据来源</th>
                  <th className="px-3 py-2 font-medium">采集方式</th>
                  <th className="px-3 py-2 font-medium">更新频率</th>
                  <th className="px-3 py-2 font-medium whitespace-nowrap">使用情况</th>
                  <th className="px-3 py-2 font-medium">备注/口径</th>
                  <th className="px-3 py-2 font-medium">操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleItems.map((r) => {
                  const dirty = isDirty(r);
                  return (
                    <tr key={r.id} style={{ borderBottom: "1px solid var(--border-color)", backgroundColor: dirty ? "rgba(46,204,113,0.08)" : "transparent" }}>
                      <td className="px-3 py-1.5 whitespace-nowrap">{dirty ? <span style={{ color: "var(--accent-green)" }}>● 修改中</span> : <span style={{ color: "var(--text-tertiary)" }}>已保存</span>}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap">
                        <select className="w-24 px-1 py-1 rounded border text-xs" style={cellInput().style}
                          value={r.category ?? ""} onChange={(e) => updateRow(r.id, { category: e.target.value || null })}>
                          <option value="">未分组</option>
                          {CATEGORY_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap font-mono">{r.table_name}</td>
                      <td className="px-3 py-1.5 whitespace-nowrap font-mono">{r.field_name}</td>
                      <td className="px-3 py-1.5 min-w-[140px]">
                        <input className={inputCls} style={cellInput().style} value={r.field_comment ?? ""}
                          onChange={(e) => updateRow(r.id, { field_comment: e.target.value || null })} />
                      </td>
                      <td className="px-3 py-1.5 min-w-[120px]">
                        <select className="w-full px-1 py-1 rounded border text-xs" style={cellInput().style}
                          value={r.data_source ?? ""} onChange={(e) => updateRow(r.id, { data_source: e.target.value || null })}>
                          <option value="">未标注</option>
                          {SOURCE_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="px-3 py-1.5 min-w-[120px]">
                        <select className="w-full px-1 py-1 rounded border text-xs" style={cellInput().style}
                          value={r.collect_method ?? ""} onChange={(e) => updateRow(r.id, { collect_method: e.target.value || null })}>
                          <option value="">未标注</option>
                          {METHOD_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="px-3 py-1.5 min-w-[100px]">
                        <select className="w-full px-1 py-1 rounded border text-xs" style={cellInput().style}
                          value={r.update_freq ?? ""} onChange={(e) => updateRow(r.id, { update_freq: e.target.value || null })}>
                          <option value="">未标注</option>
                          {FREQ_OPTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
                        </select>
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap">
                        {r.is_used ? (
                          <span className="font-mono" style={{ color: "var(--accent-green)" }}>已使用 ({r.used_count ?? 0})</span>
                        ) : (
                          <span className="font-mono" style={{ color: "var(--accent-red)" }}>未使用</span>
                        )}
                      </td>
                      <td className="px-3 py-1.5 min-w-[200px]">
                        <input className={inputCls} style={cellInput().style} value={r.notes ?? ""}
                          onChange={(e) => updateRow(r.id, { notes: e.target.value || null })} />
                      </td>
                      <td className="px-3 py-1.5 whitespace-nowrap">
                        <button onClick={() => handleRemove(r)} className="px-2 py-0.5 rounded text-[11px] font-medium"
                          style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--accent-red)" }}>删除</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
