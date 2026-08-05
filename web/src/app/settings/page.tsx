"use client";

import { useEffect, useState } from "react";
import { api, type HealthStatus } from "@/lib/api";

export default function SettingsPage() {
  const [health, setHealth] = useState<HealthStatus | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  const dbOk = health?.database === "ok";

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">设置</h1>
      <div className="card mb-6">
        <h2 className="text-lg font-semibold mb-4">系统状态</h2>
        <div className="space-y-2 text-sm">
          <div className="flex gap-2">
            <span style={{ color: "var(--text-tertiary)", width: 100 }}>API 状态：</span>
            <span style={{ color: health?.status === "ok" ? "var(--accent-green)" : "var(--accent-red)" }}>
              {health ? (health.status === "ok" ? "正常" : "异常") : "未知"}
            </span>
          </div>
          <div className="flex gap-2">
            <span style={{ color: "var(--text-tertiary)", width: 100 }}>数据库：</span>
            <span style={{ color: health ? (dbOk ? "var(--accent-green)" : "var(--accent-red)") : "var(--text-tertiary)" }}>
              {health ? (dbOk ? "正常" : "异常") : "未知"}
            </span>
          </div>
          <div className="flex gap-2">
            <span style={{ color: "var(--text-tertiary)", width: 100 }}>环境：</span>
            <span>{health?.env ?? "-"}</span>
          </div>
        </div>
      </div>
      <div className="card">
        <h2 className="text-lg font-semibold mb-4">端口规划</h2>
        <p className="text-sm space-y-1" style={{ color: "var(--text-secondary)" }}>
          Web 前端 (Next.js)：9100 | API 后端 (FastAPI)：9101 | 数据库 (PostgreSQL)：5433
        </p>
        <p className="text-xs mt-2" style={{ color: "var(--text-tertiary)" }}>
          选用新端口前请先检查 Docker 是否已占用
        </p>
      </div>
    </div>
  );
}
