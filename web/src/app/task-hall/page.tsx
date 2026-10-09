"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  api,
  type Operator,
  type TaskHallMultiJob,
  type TaskHallMultiResult,
  type TaskHallSingleTask,
} from "@/lib/api";

const LEVEL_OPTIONS = ["S", "A", "B", "C", "D"];
const LIFECYCLE_OPTIONS = ["启动期", "增长期", "热卖期", "成熟期", "下降期", "未知"];

const STATUS_STYLE: Record<string, { bg: string; color: string; label: string }> = {
  pending: { bg: "#f1f5f9", color: "#64748b", label: "待执行" },
  running: { bg: "#dbeafe", color: "#1e40af", label: "执行中" },
  done: { bg: "#dcfce7", color: "#166534", label: "已完成" },
  failed: { bg: "#fee2e2", color: "#dc2626", label: "失败" },
};

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick}
      className="px-3 py-1 rounded-md text-sm transition-all"
      style={{
        backgroundColor: active ? "var(--accent-green)" : "var(--bg-tertiary)",
        color: active ? "#fff" : "var(--text-secondary)",
        fontWeight: active ? 600 : 400,
      }}>
      {children}
    </button>
  );
}

export default function TaskHallPage() {
  // 多ASIN任务
  const [levels, setLevels] = useState<string[]>(["S", "A"]);
  const [lifecycles, setLifecycles] = useState<string[]>([]);
  const [withReport, setWithReport] = useState(true);
  const [withImage, setWithImage] = useState(false);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<{
    total: number; done: number; percent: number; current_asin: string | null; stage?: string | null;
  } | null>(null);
  const [multiResult, setMultiResult] = useState<TaskHallMultiResult | null>(null);
  const [multiMsg, setMultiMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // 单ASIN报告私发任务
  const [operators, setOperators] = useState<Operator[]>([]);
  const [singleOperator, setSingleOperator] = useState("");
  const [singleAsin, setSingleAsin] = useState("");
  const [singleRunAt, setSingleRunAt] = useState("");
  const [singleTasks, setSingleTasks] = useState<TaskHallSingleTask[]>([]);
  const [singleCreating, setSingleCreating] = useState(false);
  const [singleMsg, setSingleMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const loadTasks = useCallback(() => {
    api.calculation
      .taskHallSingleList()
      .then(r => setSingleTasks(r.tasks))
      .catch(() => setSingleTasks([]));
  }, []);

  useEffect(() => {
    api.operators
      .list({ status: true })
      .then(list => setOperators(list))
      .catch(() => setOperators([]));
    loadTasks();
  }, [loadTasks]);

  // 存在待执行/执行中的任务时，自动轮询刷新
  useEffect(() => {
    const busy = singleTasks.some(t => t.status === "pending" || t.status === "running");
    if (!busy) return;
    const timer = setInterval(loadTasks, 5000);
    return () => clearInterval(timer);
  }, [singleTasks, loadTasks]);

  // 轮询多ASIN任务直至结束（页面刷新后也能接管正在跑的任务）
  const pollMultiJob = useCallback(async (jobId: string, first?: TaskHallMultiJob) => {
    setRunning(true);
    try {
      let job: TaskHallMultiJob | undefined = first;
      for (;;) {
        if (job?.progress) setProgress(job.progress);
        if (job && job.status !== "running") break;
        await new Promise(r => setTimeout(r, 2000));
        job = (await api.calculation.getJob(jobId)) as unknown as TaskHallMultiJob;
      }
      if (job.status === "failed") {
        setMultiMsg({ ok: false, text: job.error || "任务执行失败" });
      } else {
        setMultiResult(job.stats ?? null);
        setMultiMsg({ ok: true, text: "多ASIN任务已完成" });
      }
    } catch (e) {
      setMultiMsg({ ok: false, text: e instanceof Error ? e.message : "任务执行失败" });
    } finally {
      setRunning(false);
      setProgress(null);
    }
  }, []);

  // 页面刷新后恢复最近一次多ASIN任务的进度/结果
  useEffect(() => {
    let cancelled = false;
    api.calculation
      .taskHallMultiLatest()
      .then(r => {
        if (cancelled || !r.job) return;
        const job = r.job;
        if (job.status === "running") {
          setProgress(job.progress ?? { total: 0, done: 0, percent: 0, current_asin: null, stage: "计算" });
          pollMultiJob(job.job_id, job);
        } else if (job.status === "done") {
          setMultiResult(job.stats ?? null);
          setMultiMsg({ ok: true, text: "多ASIN任务已完成" });
        } else if (job.status === "failed") {
          setMultiMsg({ ok: false, text: job.error || "任务执行失败" });
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [pollMultiJob]);

  const toggleLevel = (lv: string) =>
    setLevels(prev => (prev.includes(lv) ? prev.filter(v => v !== lv) : [...prev, lv]));
  const toggleLifecycle = (lc: string) =>
    setLifecycles(prev => (prev.includes(lc) ? prev.filter(v => v !== lc) : [...prev, lc]));

  const runMulti = async () => {
    if (levels.length === 0) {
      setMultiMsg({ ok: false, text: "请至少选择一个等级" });
      return;
    }
    setRunning(true);
    setMultiMsg(null);
    setMultiResult(null);
    setProgress({ total: 0, done: 0, percent: 0, current_asin: null, stage: "提交任务" });
    try {
      const res = await api.calculation.taskHallMulti({
        levels: levels.join(","),
        lifecycles: lifecycles.length ? lifecycles.join(",") : undefined,
        with_report: withReport,
        with_image: withReport && withImage,
      });
      await pollMultiJob(res.job_id);
    } catch (e) {
      setMultiMsg({ ok: false, text: e instanceof Error ? e.message : "任务执行失败" });
      setRunning(false);
      setProgress(null);
    }
  };

  const createSingle = async () => {
    const asin = singleAsin.trim();
    if (!asin) {
      setSingleMsg({ ok: false, text: "请填写 ASIN" });
      return;
    }
    setSingleCreating(true);
    setSingleMsg(null);
    try {
      const res = await api.calculation.taskHallSingleCreate({
        asin,
        operator: singleOperator || undefined,
        run_at: singleRunAt || undefined,
      });
      setSingleMsg({ ok: true, text: res.message });
      setSingleAsin("");
      setSingleRunAt("");
      loadTasks();
    } catch (e) {
      setSingleMsg({ ok: false, text: e instanceof Error ? e.message : "创建失败" });
    } finally {
      setSingleCreating(false);
    }
  };

  const cancelSingle = async (id: string) => {
    try {
      await api.calculation.taskHallSingleCancel(id);
      loadTasks();
    } catch (e) {
      setSingleMsg({ ok: false, text: e instanceof Error ? e.message : "取消失败" });
    }
  };

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">任务大厅</h1>

      {/* 多ASIN任务 */}
      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-1">多 ASIN 任务</h2>
        <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
          按「等级 + 生命周期」立即跑完整流程：计算 → 生成日报 → 推送飞书，可勾选附带推送日报大屏大图。
        </p>

        <div className="mb-4">
          <p className="text-sm font-medium mb-2">产品等级</p>
          <div className="flex flex-wrap gap-2">
            {LEVEL_OPTIONS.map(lv => (
              <Chip key={lv} active={levels.includes(lv)} onClick={() => toggleLevel(lv)}>{lv}</Chip>
            ))}
          </div>
        </div>

        <div className="mb-4">
          <p className="text-sm font-medium mb-2">
            生命周期 <span className="font-normal" style={{ color: "var(--text-tertiary)" }}>（不选=全部）</span>
          </p>
          <div className="flex flex-wrap gap-2">
            {LIFECYCLE_OPTIONS.map(lc => (
              <Chip key={lc} active={lifecycles.includes(lc)} onClick={() => toggleLifecycle(lc)}>{lc}</Chip>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-6 mb-4">
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" checked={withReport} onChange={e => setWithReport(e.target.checked)} />
            生成并推送日报
          </label>
          <label className="flex items-center gap-2 text-sm cursor-pointer" style={{ opacity: withReport ? 1 : 0.5 }}>
            <input type="checkbox" checked={withImage} disabled={!withReport}
              onChange={e => setWithImage(e.target.checked)} />
            附带推送大屏大图
          </label>
        </div>

        <button onClick={runMulti} disabled={running}
          className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
          style={{ backgroundColor: running ? "#94a3b8" : "var(--accent-green)" }}>
          {running ? "运行中..." : "立即运行"}
        </button>

        {multiMsg && (
          <div className="card mt-4 text-sm" style={{ borderLeft: `4px solid ${multiMsg.ok ? "var(--accent-green)" : "var(--accent-red)"}`, color: multiMsg.ok ? "inherit" : "var(--accent-red)" }}>
            {multiMsg.text}
          </div>
        )}

        {progress && progress.total > 0 && (
          <div className="card mt-4 text-sm">
            <div className="flex justify-between text-xs mb-1" style={{ color: "var(--text-tertiary)" }}>
              <span>计算进度 {progress.done}/{progress.total}</span>
              <span className="font-mono">{progress.percent}%</span>
            </div>
            <div style={{ height: 8, borderRadius: 4, backgroundColor: "var(--bg-tertiary)", overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${progress.percent}%`, backgroundColor: "var(--accent-green)", transition: "width .5s" }} />
            </div>
            {(progress.stage || progress.current_asin) && (
              <p className="text-[10px] font-mono mt-1 truncate" style={{ color: "var(--text-tertiary)" }}>
                {progress.stage ? `当前步骤: ${progress.stage}` : `正在计算: ${progress.current_asin}`}
              </p>
            )}
          </div>
        )}

        {multiResult && (
          <div className="card mt-4 text-sm" style={{ borderLeft: "4px solid var(--accent-green)" }}>
            <p className="mb-1">
              等级 <strong>{multiResult.levels}</strong>
              {multiResult.lifecycles ? <> ｜ 生命周期 <strong>{multiResult.lifecycles}</strong></> : null}
            </p>
            <p className="mb-1">
              计算完成：共 <strong>{multiResult.calc.total ?? "-"}</strong> 个，
              成功 <strong>{multiResult.calc.success ?? "-"}</strong> / 失败 <strong>{multiResult.calc.failed ?? "-"}</strong>
            </p>
            <p>
              🛒 立即采购 <strong>{multiResult.calc.immediate ?? "-"}</strong> ｜
              👀 观察 <strong>{multiResult.calc.observe ?? "-"}</strong> ｜
              ⏸ 暂停 <strong>{multiResult.calc.pause ?? "-"}</strong>
            </p>
            {multiResult.report && (
              <p className="mt-1">
                日报推送：{multiResult.report.sent ? "已推送" : `未推送（${multiResult.report.reason || "未知原因"}）`}
                {multiResult.report.sent && withImage
                  ? ` ｜ 大屏图：${multiResult.report.image_sent ? "已推送" : "未推送"}`
                  : ""}
              </p>
            )}
          </div>
        )}
      </div>

      {/* 单ASIN报告私发任务 */}
      <div className="card">
        <h2 className="text-lg font-semibold mb-1">单 ASIN 报告私发任务</h2>
        <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
          针对单个 ASIN 重拉数据、计算并私发分析报告给负责人；可指定定时时间，不填则立即执行。负责人仅作发送对象校验（需与该 ASIN 产品负责人一致）。
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-4">
          <div>
            <label className="block text-sm font-medium mb-1">负责人</label>
            <select value={singleOperator} onChange={e => setSingleOperator(e.target.value)}
              className="w-full px-3 py-1.5 rounded-md text-sm"
              style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }}>
              <option value="">不校验（按产品负责人发送）</option>
              {operators.map(op => (
                <option key={op.id} value={op.name}>{op.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">ASIN</label>
            <input value={singleAsin} onChange={e => setSingleAsin(e.target.value)}
              placeholder="如 B0XXXXXXXX"
              className="w-full px-3 py-1.5 rounded-md text-sm font-mono"
              style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }} />
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">
              定时 <span className="font-normal" style={{ color: "var(--text-tertiary)" }}>（可选）</span>
            </label>
            <input type="datetime-local" value={singleRunAt} onChange={e => setSingleRunAt(e.target.value)}
              className="w-full px-3 py-1.5 rounded-md text-sm"
              style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)", border: "1px solid var(--border-color)" }} />
          </div>
          <div className="flex items-end">
            <button onClick={createSingle} disabled={singleCreating}
              className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
              style={{ backgroundColor: singleCreating ? "#94a3b8" : "var(--accent-green)" }}>
              {singleCreating ? "创建中..." : "创建任务"}
            </button>
          </div>
        </div>

        {singleMsg && (
          <div className="card mb-4 text-sm" style={{ borderLeft: `4px solid ${singleMsg.ok ? "var(--accent-green)" : "var(--accent-red)"}`, color: singleMsg.ok ? "inherit" : "var(--accent-red)" }}>
            {singleMsg.text}
          </div>
        )}

        {singleTasks.length === 0 ? (
          <div className="text-center py-8 text-sm" style={{ color: "var(--text-tertiary)" }}>暂无任务</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                  <th className="text-left py-2 pr-3">ASIN</th>
                  <th className="text-left py-2 pr-3">负责人</th>
                  <th className="text-center py-2 pr-3">状态</th>
                  <th className="text-left py-2 pr-3">定时时间</th>
                  <th className="text-left py-2 pr-3">创建时间</th>
                  <th className="text-left py-2 pr-3">结果</th>
                  <th className="text-right py-2">操作</th>
                </tr>
              </thead>
              <tbody>
                {singleTasks.map(t => {
                  const s = STATUS_STYLE[t.status] || STATUS_STYLE.pending;
                  return (
                    <tr key={t.id} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{t.asin}</td>
                      <td className="py-2 pr-3">{t.operator || t.product_operator || "-"}</td>
                      <td className="py-2 pr-3 text-center">
                        <span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: s.bg, color: s.color }}>{s.label}</span>
                      </td>
                      <td className="py-2 pr-3 text-xs" style={{ color: "var(--text-secondary)" }}>
                        {t.run_at ? t.run_at.replace("T", " ") : "立即执行"}
                      </td>
                      <td className="py-2 pr-3 text-xs" style={{ color: "var(--text-tertiary)" }}>{t.created_at.replace("T", " ")}</td>
                      <td className="py-2 pr-3 text-xs" style={{ color: t.status === "failed" ? "var(--accent-red)" : "var(--text-secondary)" }}>
                        {t.status === "failed" ? (t.error || "执行失败") : t.status === "done" ? "已私发" : "-"}
                      </td>
                      <td className="py-2 text-right">
                        {t.status === "pending" && (
                          <button onClick={() => cancelSingle(t.id)}
                            className="px-2 py-0.5 rounded text-xs"
                            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                            取消
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
