"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Operator } from "@/lib/api";

export default function OperatorsPage() {
  const [items, setItems] = useState<Operator[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [debounced, setDebounced] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [form, setForm] = useState({ name: "", role: "", feishu_user_id: "", notes: "" });
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [syncing, setSyncing] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const load = useCallback(() => {
    setLoading(true);
    api.operators
      .list({ keyword: debounced || undefined, status: true })
      .then(setItems)
      .catch((e: Error) => setMessage({ ok: false, text: e.message }))
      .finally(() => setLoading(false));
  }, [debounced]);

  useEffect(() => { load(); }, [load]);

  const syncNow = async () => {
    setSyncing(true);
    setMessage(null);
    try {
      const res = await api.operators.sync();
      setMessage({ ok: true, text: `${res.message}：共 ${res.total} 人，新增 ${res.created} 人` });
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "同步失败" });
    } finally {
      setSyncing(false);
    }
  };

  const resetForm = () => {
    setForm({ name: "", role: "", feishu_user_id: "", notes: "" });
    setEditingId(null);
  };

  const startEdit = (op: Operator) => {
    setEditingId(op.id);
    setForm({ name: op.name, role: op.role ?? "", feishu_user_id: op.feishu_user_id ?? "", notes: op.notes ?? "" });
    setMessage(null);
  };

  const submit = async () => {
    const name = form.name.trim();
    if (!name) {
      setMessage({ ok: false, text: "请填写姓名" });
      return;
    }
    setSaving(true);
    setMessage(null);
    try {
      if (editingId != null) {
        await api.operators.update(editingId, { name, role: form.role.trim() || undefined, feishu_user_id: form.feishu_user_id.trim() || undefined, notes: form.notes.trim() || undefined });
        setMessage({ ok: true, text: "运营人员已更新" });
      } else {
        await api.operators.create({ name, role: form.role.trim() || undefined, feishu_user_id: form.feishu_user_id.trim() || undefined, notes: form.notes.trim() || undefined });
        setMessage({ ok: true, text: "运营人员已新增" });
      }
      resetForm();
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "保存失败" });
    } finally {
      setSaving(false);
    }
  };

  const toggleStatus = async (op: Operator) => {
    try {
      await api.operators.update(op.id, { status: !op.status });
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "操作失败" });
    }
  };

  const remove = async (op: Operator) => {
    if (!window.confirm(`确认删除运营人员「${op.name}」？产品中的负责人名称会保留。`)) return;
    try {
      await api.operators.remove(op.id);
      setMessage({ ok: true, text: `「${op.name}」已删除` });
      if (editingId === op.id) resetForm();
      load();
    } catch (e) {
      setMessage({ ok: false, text: e instanceof Error ? e.message : "删除失败" });
    }
  };

  const inputCls = {
    backgroundColor: "var(--bg-secondary)",
    borderColor: "var(--border-color)",
    color: "var(--text-primary)",
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">运营人员管理</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>
        存放运营人员数据，作为产品基础数据「负责人」字段的候选（负责人可单选或多选，多个姓名用逗号分隔保存）。
      </p>

      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-3">{editingId != null ? "编辑运营人员" : "新增运营人员"}</h2>
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-sm">
            姓名 *
            <input
              type="text"
              value={form.name}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
              className="block mt-1 px-3 py-1.5 rounded-md border w-44"
              style={inputCls}
            />
          </label>
          <label className="text-sm">
            岗位/角色
            <input
              type="text"
              value={form.role}
              onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
              className="block mt-1 px-3 py-1.5 rounded-md border w-44"
              style={inputCls}
            />
          </label>
          <label className="text-sm">
            飞书UID（@用）
            <input
              type="text"
              value={form.feishu_user_id}
              onChange={e => setForm(f => ({ ...f, feishu_user_id: e.target.value }))}
              placeholder="ou_xxx / on_xxx"
              className="block mt-1 px-3 py-1.5 rounded-md border w-52"
              style={inputCls}
            />
          </label>
          <label className="text-sm">
            备注
            <input
              type="text"
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              className="block mt-1 px-3 py-1.5 rounded-md border w-64"
              style={inputCls}
            />
          </label>
          <button
            onClick={submit}
            disabled={saving}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: saving ? "#94a3b8" : "var(--accent-green)" }}
          >
            {saving ? "保存中..." : editingId != null ? "保存修改" : "新增"}
          </button>
          {editingId != null && (
            <button onClick={resetForm} className="px-3 py-1.5 rounded-md text-sm font-medium" style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>
              取消
            </button>
          )}
        </div>
      </div>

      {message && (
        <div className="card mb-4 text-sm" style={{
          borderLeft: `4px solid ${message.ok ? "var(--accent-green)" : "var(--accent-red)"}`,
          color: message.ok ? "inherit" : "var(--accent-red)",
        }}>
          {message.text}
        </div>
      )}

      <div className="flex items-center justify-between mb-4">
        <input
          type="text"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          placeholder="搜索姓名 / 岗位 / 备注..."
          className="text-sm px-3 py-1.5 rounded-md border w-64"
          style={inputCls}
        />
        <span className="flex items-center gap-2">
          <button
            onClick={syncNow}
            disabled={syncing}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: syncing ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}
          >
            {syncing ? "同步中..." : "从产品负责人同步"}
          </button>
          <span className="text-sm" style={{ color: "var(--text-tertiary)" }}>共 {items.length} 人</span>
        </span>
      </div>

      {loading && <p>加载中...</p>}
      {!loading && items.length === 0 && (
        <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>暂无运营人员，请先新增</div>
      )}
      {!loading && items.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th className="text-left py-2 pr-3">姓名</th>
                <th className="text-left py-2 pr-3">岗位/角色</th>
                <th className="text-left py-2 pr-3">飞书UID</th>
                <th className="text-left py-2 pr-3">备注</th>
                <th className="text-center py-2 pr-3">状态</th>
                <th className="text-center py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map(op => (
                <tr key={op.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <td className="py-2 pr-3 font-medium">{op.name}</td>
                  <td className="py-2 pr-3 text-xs">{op.role ?? "-"}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{op.feishu_user_id ?? "-"}</td>
                  <td className="py-2 pr-3 text-xs truncate max-w-xs">{op.notes ?? "-"}</td>
                  <td className="py-2 pr-3 text-center">
                    <button
                      onClick={() => toggleStatus(op)}
                      className="px-2 py-0.5 rounded text-xs font-medium"
                      style={{
                        backgroundColor: op.status ? "rgba(34,197,94,0.12)" : "var(--bg-tertiary)",
                        color: op.status ? "var(--accent-green)" : "var(--text-tertiary)",
                      }}
                      title={op.status ? "点击停用" : "点击启用"}
                    >
                      {op.status ? "启用" : "停用"}
                    </button>
                  </td>
                  <td className="py-2 text-center whitespace-nowrap">
                    <button
                      onClick={() => startEdit(op)}
                      className="px-2 py-1 rounded-md text-xs font-medium mr-2"
                      style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => remove(op)}
                      className="px-2 py-1 rounded-md text-xs font-medium"
                      style={{ backgroundColor: "rgba(239,68,68,0.12)", color: "var(--accent-red)" }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
