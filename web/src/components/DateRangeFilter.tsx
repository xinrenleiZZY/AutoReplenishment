"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";

/** 日期区间筛选值（与后端 filters 的 range 结构一致） */
export interface DateRangeValue {
  min?: string;
  max?: string;
}

interface DateRangeFilterProps {
  /** 当前区间值，形如 { min: "2026-08-04", max: "2026-09-17" } */
  value: DateRangeValue;
  /** 选中完整区间或点击清除时回调 */
  onChange: (v: DateRangeValue) => void;
  /** 与其它筛选控件保持一致的输入框样式 */
  style?: CSSProperties;
}

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];

/** 快捷区间：相对今天的起止偏移天数（0 为今天） */
const PRESETS: { label: string; start: number; end: number }[] = [
  { label: "今日", start: 0, end: 0 },
  { label: "昨日", start: -1, end: -1 },
  { label: "最近3天", start: -2, end: 0 },
  { label: "最近7天", start: -6, end: 0 },
  { label: "最近45天", start: -44, end: 0 },
];

/** Date → "YYYY-MM-DD"（本地时区） */
function fmt(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

/** "YYYY-MM-DD..." → Date（本地时区），无法解析返回 null */
function parse(s?: string): Date | null {
  const m = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(String(s ?? ""));
  if (!m) return null;
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/** 相对今天偏移若干天 */
function shiftToday(days: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d;
}

/** 生成某个月的日历格子：首尾补 null 使其对齐整周 */
function monthCells(year: number, month: number): (Date | null)[] {
  const list: (Date | null)[] = [];
  for (let i = 0; i < new Date(year, month, 1).getDay(); i++) list.push(null);
  const last = new Date(year, month + 1, 0).getDate();
  for (let d = 1; d <= last; d++) list.push(new Date(year, month, d));
  while (list.length % 7 !== 0) list.push(null);
  return list;
}

export default function DateRangeFilter({ value, onChange, style }: DateRangeFilterProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DateRangeValue>(value);
  const [hover, setHover] = useState<string | null>(null);
  const [viewDate, setViewDate] = useState<Date>(() => parse(value.min) ?? parse(value.max) ?? new Date());
  const boxRef = useRef<HTMLDivElement>(null);

  // 展开面板时把外部值同步进草稿，并把视图定位到已选月份
  const toggle = () => {
    if (!open) {
      setDraft(value);
      setHover(null);
      setViewDate(parse(value.min) ?? parse(value.max) ?? new Date());
    }
    setOpen(o => !o);
  };

  // 点击面板外关闭
  useEffect(() => {
    if (!open) return;
    const onDocDown = (e: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, [open]);

  // 当前待显示的区间（只选了起点时用悬停日做预览）
  const bounds = (): [string | null, string | null] => {
    const s = draft.min ?? null;
    const e = draft.max ?? (draft.min ? hover : null);
    if (s && e) return s <= e ? [s, e] : [e, s];
    return [s, e];
  };

  const dayState = (d: Date): "start" | "end" | "mid" | null => {
    const [lo, hi] = bounds();
    const ds = fmt(d);
    if (lo && ds === lo) return "start";
    if (hi && ds === hi) return "end";
    if (lo && hi && ds > lo && ds < hi) return "mid";
    return null;
  };

  const pickDay = (d: Date) => {
    const s = fmt(d);
    // 未选起点 / 区间已完整 → 重新开始选起点
    if (!draft.min || draft.max) {
      setDraft({ min: s });
      setHover(null);
      return;
    }
    const next: DateRangeValue = s < draft.min ? { min: s, max: draft.min } : { min: draft.min, max: s };
    setDraft(next);
    onChange(next);
    setOpen(false);
  };

  const applyPreset = (p: { start: number; end: number }) => {
    const next: DateRangeValue = { min: fmt(shiftToday(p.start)), max: fmt(shiftToday(p.end)) };
    setDraft(next);
    setViewDate(parse(next.min) as Date);
    onChange(next);
    setOpen(false);
  };

  const clear = () => {
    setDraft({});
    onChange({});
    setOpen(false);
  };

  const text = value.min && value.max
    ? `${value.min} ~ ${value.max}`
    : value.min
      ? `${value.min} ~`
      : "选择日期";

  const mutedColor = "var(--text-tertiary)";
  const today = fmt(new Date());
  const [draftLo, draftHi] = bounds();

  // 渲染单个月份；nav 决定翻页按钮放在该月的左侧（prev）还是右侧（next）
  const renderMonth = (year: number, month: number, nav: "prev" | "next") => (
    <div className="shrink-0">
      <div className="flex items-center justify-between mb-2 text-xs font-semibold" style={{ color: "var(--text-primary)" }}>
        <div className="flex items-center gap-0.5">
          {nav === "prev" && (
            <>
              <button type="button" className="px-1.5 py-0.5 rounded"
                onClick={() => setViewDate(new Date(viewDate.getFullYear() - 1, viewDate.getMonth(), 1))}>«</button>
              <button type="button" className="px-1.5 py-0.5 rounded"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() - 1, 1))}>‹</button>
            </>
          )}
        </div>
        <span className="whitespace-nowrap">{year} 年 {month + 1} 月</span>
        <div className="flex items-center gap-0.5">
          {nav === "next" && (
            <>
              <button type="button" className="px-1.5 py-0.5 rounded"
                onClick={() => setViewDate(new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1))}>›</button>
              <button type="button" className="px-1.5 py-0.5 rounded"
                onClick={() => setViewDate(new Date(viewDate.getFullYear() + 1, viewDate.getMonth(), 1))}>»</button>
            </>
          )}
        </div>
      </div>

      <div className="grid gap-y-1 mb-1" style={{ gridTemplateColumns: "repeat(7, 1.75rem)" }}>
        {WEEKDAYS.map(w => (
          <span key={w} className="text-center text-xs" style={{ color: mutedColor }}>{w}</span>
        ))}
      </div>

      <div className="grid gap-y-1" style={{ gridTemplateColumns: "repeat(7, 1.75rem)" }}>
        {monthCells(year, month).map((d, i) => {
          if (!d) return <span key={i} />;
          const ds = fmt(d);
          const st = dayState(d);
          const solid = st === "start" || st === "end";
          return (
            <button
              key={i}
              type="button"
              onClick={() => pickDay(d)}
              onMouseEnter={() => setHover(draft.min && !draft.max ? ds : null)}
              className="w-7 h-7 rounded text-xs flex items-center justify-center"
              style={{
                backgroundColor: solid
                  ? "var(--accent-blue)"
                  : st === "mid"
                    ? "rgba(59,130,246,0.16)"
                    : "transparent",
                color: solid ? "#fff" : "var(--text-primary)",
                border: ds === today && !solid ? `1px solid ${mutedColor}` : "1px solid transparent",
                fontWeight: ds === today ? 700 : 400,
              }}
            >
              {d.getDate()}
            </button>
          );
        })}
      </div>
    </div>
  );

  const rightMonth = new Date(viewDate.getFullYear(), viewDate.getMonth() + 1, 1);

  return (
    <div className="relative" ref={boxRef}>
      <button
        type="button"
        onClick={toggle}
        className="w-56 px-1.5 py-1 rounded border text-xs flex items-center justify-between gap-1"
        style={style}
      >
        <span className="truncate" style={{ color: value.min ? "var(--text-primary)" : mutedColor }}>{text}</span>
        <span style={{ color: mutedColor }}>▾</span>
      </button>

      {open && (
        <div
          className="absolute z-50 mt-1 rounded-lg border shadow-lg"
          style={{ top: "100%", left: 0, backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)" }}
        >
          <div className="flex">
            {/* 快捷区间 */}
            <div className="flex flex-col gap-1 p-2 border-r shrink-0" style={{ borderColor: "var(--border-color)" }}>
              {PRESETS.map(p => (
                <button
                  key={p.label}
                  type="button"
                  onClick={() => applyPreset(p)}
                  className="px-2 py-1 rounded text-xs text-left whitespace-nowrap"
                  style={{ color: "var(--text-secondary)" }}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                onClick={clear}
                className="px-2 py-1 rounded text-xs text-left whitespace-nowrap"
                style={{ color: "var(--accent-red)" }}
              >
                清除
              </button>
            </div>

            {/* 日历：左右两个月并列 */}
            <div className="p-3 shrink-0" onMouseLeave={() => setHover(null)}>
              <div className="flex gap-5">
                {renderMonth(viewDate.getFullYear(), viewDate.getMonth(), "prev")}
                {renderMonth(rightMonth.getFullYear(), rightMonth.getMonth(), "next")}
              </div>

              <p className="mt-2 text-xs" style={{ color: mutedColor }}>
                {draftLo && !draftHi ? "请选择结束日期" : `${draftLo ?? "-"} ~ ${draftHi ?? "-"}`}
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
