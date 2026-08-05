/** API 请求辅助函数与类型定义 */

export interface Product {
  asin: string;
  product_name: string;
  category: string | null;
  sub_category: string | null;
  life_cycle: string | null;
  product_level: string | null;
  calc_frequency: string | null;
  product_type: string | null;
  product_stage: string | null;
  festival: string | null;
  core_months: string | null;
  lead_time: number | null;
  box_quantity: number | null;
  min_order_qty: number | null;
  list_date: string | null;
  status: boolean;
  operator: string | null;
  profit_rate: number | null;
  created_at: string;
  updated_at: string;
}

export interface CalculationResult {
  id: number;
  asin: string;
  calc_date: string;
  forecast_total: number | null;
  available_stock: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  urgency_score: number | null;
  purchase_trigger: string | null;
  suggested_qty: number | null;
  batch_plan: string | null;
  purchase_score: number | null;
  purchase_level: string | null;
  score_detail: string | null;
}

export interface DailyAlert {
  asin: string;
  product_name: string | null;
  purchase_score: number | null;
  suggested_qty: number;
  purchase_trigger?: string | null;
}

export interface DailyReport {
  calc_date: string;
  total_asins: number;
  immediate_count: number;
  observe_count: number;
  pause_count: number;
  top_alerts: DailyAlert[];
  results: CalculationResult[];
}

export interface SalesData {
  id: number;
  asin: string;
  date: string;
  sales_qty: number;
  sales_amount: number | null;
  data_source: string | null;
}

export interface FestivalCalendar {
  id: number;
  festival: string;
  festival_en: string | null;
  listing_start: string | null;
  festival_date: string | null;
  festival_end: string | null;
  hot_period: string | null;
  hot_start_month: number | null;
  hot_end_month: number | null;
  launch_stage: string | null;
  growth_stage: string | null;
  mature_stage: string | null;
  decline_stage: string | null;
  notes: string | null;
}

export interface CategoryLeadtime {
  id: number;
  level1_category: string;
  level2_category: string | null;
  lead_time_min: number | null;
  lead_time_max: number | null;
  notes: string | null;
}

export interface SeasonalCurve {
  id: number;
  festival: string;
  sub_category: string;
  month_distribution: string;
  sample_count: number;
  sample_asins: string | null;
  updated_at: string | null;
}

export interface SyncLog {
  id: number;
  sync_type: string;
  status: string;
  total_count: number | null;
  success_count: number | null;
  error_message: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface HealthStatus {
  status: string;
  env?: string;
  database?: string;
}

export interface BatchStats {
  total: number;
  due?: number;
  skipped?: number;
  success: number;
  failed: number;
  immediate: number;
  observe: number;
  pause: number;
  by_level?: Record<string, { total: number; due: number; success: number; failed: number; frequency_days: number }>;
}

export interface LifecycleStats {
  total: number;
  by_life_cycle: { label: string; count: number }[];
  by_product_level: { label: string; count: number }[];
}

export interface LevelDueInfo {
  total: number;
  due: number;
  frequency_days: number;
}

export interface DueStats {
  date: string;
  total: number;
  due: number;
  by_level: Record<string, LevelDueInfo>;
}

export interface HistoryItem {
  calc_date: string;
  purchase_score: number | null;
  suggested_qty: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  purchase_level: string | null;
  purchase_trigger: string | null;
}

export interface RiskItem {
  asin: string;
  product_level: string | null;
  purchase_level: string | null;
  inventory_days?: number;
  replenishment_cycle?: number;
  profit_rate?: number;
  reason: string;
}

export interface RiskReport {
  date: string;
  stockout: RiskItem[];
  overstock: RiskItem[];
  profit: RiskItem[];
}

function toQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return "";
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return qs ? `?${qs}` : "";
}

async function fetchJSON<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch {
      /* 忽略非 JSON 错误体 */
    }
    throw new Error(message);
  }
  return res.json();
}

export const api = {
  health: () => fetchJSON<HealthStatus>("/health"),
  products: {
    list: (params?: { skip?: number; limit?: number; keyword?: string; status?: boolean; life_cycle?: string; product_level?: string }) =>
      fetchJSON<Product[]>(`/api/v1/products/${toQuery({ limit: 200, ...params })}`),
    get: (asin: string) => fetchJSON<Product>(`/api/v1/products/${asin}`),
    lifecycleStats: () => fetchJSON<LifecycleStats>("/api/v1/products/stats/lifecycle"),
  },
  sales: {
    list: (asin: string, start?: string, end?: string) =>
      fetchJSON<SalesData[]>(`/api/v1/sales/${asin}${toQuery({ start_date: start, end_date: end })}`),
  },
  calculation: {
    results: (params?: { asin?: string; purchase_level?: string; skip?: number; limit?: number }) =>
      fetchJSON<CalculationResult[]>(`/api/v1/calculation/results${toQuery(params)}`),
    latest: (asin: string) => fetchJSON<CalculationResult>(`/api/v1/calculation/results/${asin}/latest`),
    latestSteps: (asin: string) =>
      fetchJSON<{ calculation_id: number; asin: string; steps: unknown[] }>(
        `/api/v1/calculation/results/${asin}/latest/steps`
      ),
    trigger: (asin: string) =>
      fetchJSON<{ message: string; data: CalculationResult }>(`/api/v1/calculation/trigger/${asin}`, { method: "POST" }),
    triggerBatch: () =>
      fetchJSON<{ message: string; stats: BatchStats }>("/api/v1/calculation/trigger/batch", { method: "POST" }),
    dueStats: () => fetchJSON<DueStats>("/api/v1/calculation/due-stats"),
    triggerDue: () =>
      fetchJSON<{ message: string; stats: BatchStats }>("/api/v1/calculation/trigger/due", { method: "POST" }),
    history: (asin: string) => fetchJSON<HistoryItem[]>(`/api/v1/calculation/results/${asin}/history`),
    risks: () => fetchJSON<RiskReport>("/api/v1/calculation/risks"),
    dailyReport: (includeResults = true) =>
      fetchJSON<DailyReport>(`/api/v1/calculation/daily-report${includeResults ? "" : "?include_results=false"}`),
    pushReport: () =>
      fetchJSON<{ message: string; summary: { total_asins: number; immediate_count: number; observe_count: number; pause_count: number } }>(
        "/api/v1/calculation/daily-report/push",
        { method: "POST" }
      ),
  },
  festivalCalendar: {
    list: () => fetchJSON<FestivalCalendar[]>("/api/v1/festival-calendar/?limit=200"),
  },
  categoryLeadtimes: {
    list: () => fetchJSON<CategoryLeadtime[]>("/api/v1/category-leadtimes/?limit=200"),
  },
  seasonalCurves: {
    list: (festival?: string) =>
      fetchJSON<SeasonalCurve[]>(`/api/v1/seasonal-curves/${toQuery({ festival, limit: 100 })}`),
  },
  syncLogs: {
    list: () => fetchJSON<SyncLog[]>("/api/v1/sync-logs/?limit=50"),
  },
};
