"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

interface NavItem { href: string; label: string; icon: string; }

type NavEntry = { type: "link"; href: string; label: string; icon: string }
  | { type: "group"; label: string; icon: string; pathPrefix: string; children: NavItem[] };

const navStructure: NavEntry[] = [
  { type: "link", href: "/dashboard", label: "仪表盘", icon: "📊" },
  {
    type: "group", label: "产品管理", icon: "📦", pathPrefix: "/products",
    children: [
      { href: "/products", label: "产品列表", icon: "📋" },
      { href: "/lifecycle", label: "生命周期", icon: "🔄" },
      { href: "/category-leadtimes", label: "分类工期", icon: "⏱️" },
    ],
  },
  {
    type: "group", label: "数据分析", icon: "🔬", pathPrefix: "/calculation",
    children: [
      { href: "/calculation", label: "计算结果", icon: "📊" },
      { href: "/inventory", label: "库存健康", icon: "📦" },
      { href: "/seasonal-curves", label: "季节曲线", icon: "📈" },
    ],
  },
  {
    type: "group", label: "日历与时间", icon: "📅", pathPrefix: "/festival",
    children: [
      { href: "/festival-calendar", label: "节日日历", icon: "🗓️" },
      { href: "/time-axis", label: "销售时间轴", icon: "⏳" },
    ],
  },
  { type: "link", href: "/daily-report", label: "采购日报", icon: "📋" },
  { type: "link", href: "/sync-logs", label: "同步日志", icon: "📜" },
  { type: "link", href: "/settings", label: "设置", icon: "⚙️" },
];

export default function NavSidebar() {
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() => {
    const groups: Record<string, boolean> = {};
    for (const entry of navStructure) {
      if (entry.type === "group" && pathname.startsWith(entry.pathPrefix)) {
        groups[entry.pathPrefix] = true;
      }
    }
    return groups;
  });

  const toggleGroup = (prefix: string) => {
    setOpenGroups(p => ({ ...p, [prefix]: !p[prefix] }));
  };

  const isActive = (href: string) => {
    if (href === "/products") return pathname === "/products" || pathname.startsWith("/products/[");
    if (href === "/calculation") return pathname === "/calculation" || pathname.startsWith("/calculation/[");
    return pathname === href;
  };
  const isGroupActive = (prefix: string) => pathname.startsWith(prefix);

  return (
    <>
      <button onClick={() => setMobileOpen(!mobileOpen)}
        className="fixed top-3 left-3 z-50 lg:hidden flex items-center justify-center w-9 h-9 rounded-md"
        style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>
        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          {mobileOpen ? (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          ) : (
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
          )}
        </svg>
      </button>
      {mobileOpen && <div className="fixed inset-0 bg-black/50 z-30 lg:hidden" onClick={() => setMobileOpen(false)} />}

      <aside className={`fixed top-0 left-0 z-40 h-full w-64 transform transition-transform duration-200 ease-in-out ${mobileOpen ? "translate-x-0" : "-translate-x-full"} lg:translate-x-0`}
        style={{ backgroundColor: "var(--bg-secondary)", borderRight: "1px solid var(--border-color)" }}
      >
        <div className="p-5 border-b" style={{ borderColor: "var(--border-color)" }}>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center text-sm font-bold" style={{ backgroundColor: "var(--accent-green)", color: "#fff" }}>A</div>
            <div>
              <h1 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>自动补货决策</h1>
              <p className="text-xs" style={{ color: "var(--text-tertiary)" }}>v1.0.0</p>
            </div>
          </div>
        </div>

        <nav className="p-3 overflow-y-auto" style={{ height: "calc(100% - 90px)" }}>
          <ul className="space-y-1">
            {navStructure.map((entry) => {
              if (entry.type === "link") {
                const active = isActive(entry.href);
                return (
                  <li key={entry.href}>
                    <Link href={entry.href} onClick={() => setMobileOpen(false)}
                      className="flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-all"
                      style={{
                        backgroundColor: active ? "var(--bg-tertiary)" : "transparent",
                        color: active ? "var(--text-primary)" : "var(--text-secondary)",
                        fontWeight: active ? 600 : 400,
                      }}
                    >
                      <span className="text-lg">{entry.icon}</span>{entry.label}
                    </Link>
                  </li>
                );
              }
              const gActive = isGroupActive(entry.pathPrefix);
              const expanded = openGroups[entry.pathPrefix] || gActive;
              return (
                <li key={entry.label}>
                  <button onClick={() => toggleGroup(entry.pathPrefix)}
                    className="flex items-center justify-between w-full px-3 py-2 rounded-md text-sm transition-all"
                    style={{ backgroundColor: gActive ? "var(--bg-tertiary)" : "transparent", color: gActive ? "var(--text-primary)" : "var(--text-secondary)", fontWeight: gActive ? 600 : 400 }}>
                    <span className="flex items-center gap-3"><span className="text-lg">{entry.icon}</span>{entry.label}</span>
                    <svg className={`w-4 h-4 transition-transform duration-200 ${expanded ? "rotate-90" : ""}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                    </svg>
                  </button>
                  <div className={`overflow-hidden transition-all duration-200 ${expanded ? "max-h-64 opacity-100 mt-1" : "max-h-0 opacity-0"}`}>
                    <ul className="ml-2 space-y-1 border-l-2" style={{ borderColor: "var(--border-color)" }}>
                      {entry.children.map(child => {
                        const childActive = isActive(child.href);
                        return (
                          <li key={child.href}>
                            <Link href={child.href} onClick={() => setMobileOpen(false)}
                              className="flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-all"
                              style={{ backgroundColor: childActive ? "var(--bg-tertiary)" : "transparent", color: childActive ? "var(--text-primary)" : "var(--text-secondary)" }}>
                              <span className="text-sm">{child.icon}</span>{child.label}
                            </Link>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                </li>
              );
            })}
          </ul>
        </nav>
      </aside>
    </>
  );
}
