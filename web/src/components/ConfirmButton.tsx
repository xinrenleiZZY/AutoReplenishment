"use client";

import { useState, type CSSProperties, type ReactNode } from "react";

type Props = {
  onConfirm: () => void | Promise<void>;
  children: ReactNode;
  /** 第一次点击后显示的确认文案 */
  confirmText?: string;
  className?: string;
  style?: CSSProperties;
  disabled?: boolean;
  title?: string;
};

/**
 * Phase 3 / B-08：关键操作二次确认按钮。
 *
 * 第一次点击不会直接执行，而是切换成「确认执行？[确认][取消]」，
 * 避免误触触发计算 / 改参数 / 推送日报这类高风险动作。
 */
export default function ConfirmButton({
  onConfirm,
  children,
  confirmText = "确认执行？",
  className,
  style,
  disabled,
  title,
}: Props) {
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);

  if (!pending) {
    return (
      <button
        type="button"
        className={className}
        style={style}
        disabled={disabled}
        title={title}
        onClick={() => setPending(true)}
      >
        {children}
      </button>
    );
  }

  return (
    <span className="inline-flex items-center gap-1">
      <span className="text-xs" style={{ color: "var(--accent-red)" }}>{confirmText}</span>
      <button
        type="button"
        className="px-2 py-1 rounded text-xs font-medium text-white"
        style={{ backgroundColor: "var(--accent-red)" }}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await onConfirm();
          } finally {
            setBusy(false);
            setPending(false);
          }
        }}
      >
        {busy ? "执行中…" : "确认"}
      </button>
      <button
        type="button"
        className="px-2 py-1 rounded text-xs"
        style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
        onClick={() => setPending(false)}
      >
        取消
      </button>
    </span>
  );
}
