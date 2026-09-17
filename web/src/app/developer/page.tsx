"use client";

import Link from "next/link";
import WatermarkBanner from "@/components/WatermarkBanner";
import { useAppInfo } from "@/lib/app-info";

export default function DeveloperPage() {
  const appInfo = useAppInfo();
  return (
    <div className="space-y-6">
      <WatermarkBanner title="个人中心" subtitle="✦ IT-钟 · 系统作者" />

      <div
        className="rounded-xl p-8 flex flex-col items-center text-center"
        style={{
          backgroundColor: "var(--card-bg)",
          border: "1px solid var(--border-color)",
          boxShadow: "var(--card-shadow)",
        }}
      >
        <div
          className="w-24 h-24 rounded-full flex items-center justify-center overflow-hidden mb-4"
          style={{ boxShadow: "0 0 0 4px var(--endspace-accent-dim)" }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/创始人.png"
            alt="创始人头像"
            className="w-full h-full object-cover"
          />
        </div>

        <h2 className="text-xl font-semibold mb-1" style={{ color: "var(--text-primary)" }}>
          IT-钟
        </h2>
        <p className="text-sm mb-1" style={{ color: "var(--text-secondary)" }}>
          全栈开发者 / 系统架构师 / 自动化专家
        </p>
        <p className="text-xs mb-6" style={{ color: "var(--text-tertiary)" }}>
          自动补货决策系统 v{appInfo?.version ?? "3.9.17"} · AI 驱动的跨境补货决策平台
        </p>

        <div className="flex flex-wrap gap-4 mb-8">
          <a
            href="https://github.com/xinrenleiZZY"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90"
            style={{ backgroundColor: "#24292e" }}
          >
            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
              <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z" />
            </svg>
            GitHub
          </a>
          <a
            href="https://www.juxingspacestation.top/"
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-2 px-5 py-2.5 rounded-lg text-sm font-medium text-white transition-opacity hover:opacity-90"
            style={{ backgroundColor: "var(--accent-blue)" }}
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
            </svg>
            个人博客
          </a>
        </div>

        <div className="w-full max-w-md">
          <h3 className="text-sm font-semibold mb-3" style={{ color: "var(--text-primary)" }}>
            技术栈
          </h3>
          <div className="flex flex-wrap justify-center gap-2">
            {["Python", "FastAPI", "Next.js", "React", "TypeScript", "PostgreSQL", "Docker", "飞书API", "SQLAlchemy", "ECharts", "OpenClaw"].map((tech) => (
              <span key={tech} className="px-3 py-1 rounded-full text-xs font-medium"
                style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-secondary)" }}>
                {tech}
              </span>
            ))}
          </div>
        </div>
      </div>

      <div
        className="rounded-xl overflow-hidden"
        style={{
          backgroundColor: "var(--card-bg)",
          border: "1px solid var(--border-color)",
          boxShadow: "var(--card-shadow)",
        }}
      >
        <div className="px-5 py-3 flex items-center justify-between" style={{ borderBottom: "1px solid var(--border-color)" }}>
          <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            🌐 IT-钟的个人博客
          </h3>
          <a
            href="https://www.juxingspacestation.top/"
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs"
            style={{ color: "var(--accent-blue)" }}
          >
            新窗口打开 →
          </a>
        </div>
        <div
          style={{
            position: "relative",
            width: "100%",
            height: "450px",
            overflow: "hidden",
          }}
        >
          <iframe
            src="https://www.juxingspacestation.top"
            width="133.333%"
            height="600px"
            style={{
              border: "none",
              display: "block",
              transform: "scale(0.75)",
              transformOrigin: "top left",
            }}
            title="IT-钟的个人博客"
            loading="lazy"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          />
        </div>
      </div>

      <div className="text-center">
        <Link
          href="/dashboard"
          className="inline-flex items-center gap-1 text-sm underline hover:no-underline"
          style={{ color: "var(--accent-blue)" }}
        >
          ← 返回仪表盘
        </Link>
      </div>
    </div>
  );
}
