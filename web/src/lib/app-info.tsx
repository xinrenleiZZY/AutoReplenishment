"use client";

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type AppInfo } from "./api";

/**
 * 应用信息上下文：启动时拉取 /api/v1/app-info（含版本号），
 * 供侧边栏、个人中心等处动态展示版本号，避免写死。
 */
const AppInfoContext = createContext<AppInfo | null>(null);

export function AppInfoProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<AppInfo | null>(null);

  useEffect(() => {
    api
      .appInfo()
      .then(setInfo)
      .catch(() => {
        /* 拉取失败时保持 null，前端回退展示静态版本号 */
      });
  }, []);

  return <AppInfoContext.Provider value={info}>{children}</AppInfoContext.Provider>;
}

export function useAppInfo() {
  return useContext(AppInfoContext);
}
