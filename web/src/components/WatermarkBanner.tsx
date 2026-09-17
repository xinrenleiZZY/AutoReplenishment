"use client";

import { useState, useEffect } from "react";

/**
 * WatermarkBanner - 顶部水印滚动横幅
 * artistic：标题渲染为艺术彩字（逐字渐变流光），悬浮更亮
 * marquee：是否展示背景滚动水印（默认展示）
 * onClick：标题可点击
 */
export default function WatermarkBanner({
  title = "仪表盘",
  subtitle,
  artistic = false,
  marquee = true,
  onClick,
}: {
  title?: string;
  subtitle?: string;
  artistic?: boolean;
  marquee?: boolean;
  onClick?: () => void;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) return null;

  const ART_COLORS = ["#22d3ee", "#34d399", "#fbbf24", "#a78bfa", "#f87171"];

  return (
    <div className="relative mb-6 overflow-hidden rounded-xl border" style={{ borderColor: "var(--border-color)" }}>
      {marquee && (
        <div
          className="pointer-events-none absolute inset-0 flex items-center overflow-hidden opacity-[0.06]"
          style={{ fontFamily: "'JetBrains Mono', 'Consolas', monospace" }}
        >
          <div className="animate-marquee whitespace-nowrap">
            <span className="select-none text-[6rem] font-black leading-none" style={{ color: "var(--text-primary)" }}>
              AUTO_REPLENISH<span className="mx-[4vw]" style={{ color: "var(--text-tertiary)" }}>◆</span>
              AUTO_REPLENISH<span className="mx-[4vw]" style={{ color: "var(--text-tertiary)" }}>◆</span>
              AUTO_REPLENISH<span className="mx-[4vw]" style={{ color: "var(--text-tertiary)" }}>◆</span>
              AUTO_REPLENISH<span className="mx-[4vw]" style={{ color: "var(--text-tertiary)" }}>◆</span>
              AUTO_REPLENISH<span className="mx-[4vw]" style={{ color: "var(--text-tertiary)" }}>◆</span>
            </span>
          </div>
        </div>
      )}

      <div className="relative z-10 px-6 py-8">
        {artistic ? (
          <div
            className="artistic-title inline-block"
            role="button"
            tabIndex={0}
            onClick={onClick}
            onKeyDown={(e) => {
              if (e.key === "Enter" && onClick) onClick();
            }}
          >
            <h1 className="text-2xl font-bold">
              {title.split("").map((ch, i) => (
                <span
                  key={i}
                  className="artistic-char"
                  style={{ animationDelay: `${i * 0.35}s`, color: ART_COLORS[i % ART_COLORS.length] }}
                >
                  {ch}
                </span>
              ))}
            </h1>
            {subtitle && (
              <p className="text-[11px] mt-1 tracking-widest" style={{ color: "var(--text-tertiary)" }}>
                {subtitle}
              </p>
            )}
          </div>
        ) : (
          <h1 className="text-2xl font-bold" style={{ color: "var(--text-primary)" }}>
            {title}
          </h1>
        )}
        <div className="mt-2 h-0.5 w-16" style={{ background: "var(--endspace-accent)" }} />
      </div>
    </div>
  );
}
