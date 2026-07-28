/** API 请求辅助函数 */
const API_BASE = "";

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export const api = {
  products: {
    list: (params?: string) => fetchJSON<any[]>(`/api/v1/products/${params || "?limit=200"}`),
    get: (asin: string) => fetchJSON<any>(`/api/v1/products/${asin}`),
  },
  sales: {
    list: (asin: string, start?: string, end?: string) =>
      fetchJSON<any[]>(`/api/v1/sales/${asin}?start_date=${start || ""}&end_date=${end || ""}`),
  },
  calculation: {
    results: (asin?: string, level?: string) => {
      let p = "/api/v1/calculation/results";
      const q = [];
      if (asin) q.push(`asin=${asin}`);
      if (level) q.push(`purchase_level=${level}`);
      if (q.length) p += `?${q.join("&")}`;
      return fetchJSON<any[]>(p);
    },
    latest: (asin: string) => fetchJSON<any>(`/api/v1/calculation/results/${asin}/latest`),
    trigger: (asin: string) => fetchJSON<any>(`/api/v1/calculation/trigger/${asin}`, { method: "POST" }),
    triggerBatch: () => fetchJSON<any>("/api/v1/calculation/trigger/batch", { method: "POST" }),
    dailyReport: () => fetchJSON<any>("/api/v1/calculation/daily-report"),
    pushReport: () => fetchJSON<any>("/api/v1/calculation/daily-report/push", { method: "POST" }),
  },
};
