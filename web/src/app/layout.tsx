import type { Metadata } from "next";
import "./globals.css";
import NavSidebar from "@/components/NavSidebar";
import LoadingCover from "@/components/LoadingCover";
import { AppInfoProvider } from "@/lib/app-info";

export const metadata: Metadata = {
  title: "自动补货决策系统",
  description: "AI驱动的跨境电商自动补货决策平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>
        <LoadingCover />
        <AppInfoProvider>
          <div className="min-h-screen" style={{ backgroundColor: "var(--bg-primary)" }}>
            <NavSidebar />
            <main className="lg:pl-64">
              <div className="mx-auto max-w-page px-4 py-6 sm:px-6 lg:px-8">
                {children}
              </div>
            </main>
          </div>
        </AppInfoProvider>
      </body>
    </html>
  );
}
