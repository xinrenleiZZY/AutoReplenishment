/** API 请求辅助函数与类型定义 */

export interface Product {
  asin: string;
  lx_id: number | null;
  store_id: number | null;
  product_name: string;
  listing_title: string | null;
  msku: string | null;
  local_sku: string | null;
  fnsku: string | null;
  mid: string | null;
  amz_product_id: string | null;
  amz_product_id_type_text: string | null;
  parent_asin: string | null;
  product_id: string | null;
  product_relation_id: string | null;
  id_hash: string | null;
  local_name: string | null;
  model: string | null;
  variant: string | null;
  variant_text: string | null;
  remark: string | null;
  amz_product_type: string | null;
  price: string | null;
  listing_price: string | null;
  landed_price: string | null;
  regular_price: string | null;
  list_price: string | null;
  b2b_price: string | null;
  b2b_price_discount: string | null;
  fba_fee: string | null;
  report_fba_fee: string | null;
  referral_fee: string | null;
  shipping: string | null;
  points: string | null;
  history_price: string | null;
  history_price_source: string | null;
  currency_symbol: string | null;
  total_volume: number | null;
  yesterday_volume: number | null;
  seven_volume: number | null;
  fourteen_volume: number | null;
  thirty_volume: number | null;
  average_seven_volume: number | null;
  average_fourteen_volume: number | null;
  average_thirty_volume: number | null;
  yesterday_amount: number | null;
  seven_amount: number | null;
  fourteen_amount: number | null;
  thirty_amount: number | null;
  yesterday_spend: number | null;
  seven_spend: number | null;
  fourteen_spend: number | null;
  thirty_spend: number | null;
  afn_fulfillable_quantity: number | null;
  afn_reserved_quantity: number | null;
  reserved_fc_transfers: number | null;
  reserved_fc_processing: number | null;
  reserved_customerorders: number | null;
  afn_inbound_shipped_quantity: number | null;
  afn_unsellable_quantity: number | null;
  afn_inbound_working_quantity: number | null;
  afn_inbound_receiving_quantity: number | null;
  quantity: number | null;
  rank: number | null;
  seller_rank: number | null;
  category_rank: string | null;
  small_rank: string | null;
  seller_category: string | null;
  category_url: string | null;
  stars: number | null;
  reviews_num: number | null;
  open_date_time: string | null;
  first_order_time: string | null;
  first_order_type: string | null;
  first_order_update: string | null;
  on_sale_time: string | null;
  create_time: string | null;
  update_time: string | null;
  category_id: number | null;
  brand_id: number | null;
  brand_name: string | null;
  category: string | null;
  sub_category: string | null;
  brand: string | null;
  shop: string | null;
  marketplace: string | null;
  seller_name: string | null;
  store_type: string | null;
  fulfillment_channel_type: string | null;
  status_text: string | null;
  is_delete: string | null;
  principal_list: string | null;
  principal_uids: string | null;
  permission_user_info: string | null;
  product_creator_realname: string | null;
  product_developer: string | null;
  icon: string | null;
  tags: string | null;
  supplier_name: string | null;
  cost_price: string | null;
  acos_30d: number | null;
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
  primary_operator: string | null;
  profit_rate: number | null;
  created_at: string;
  updated_at: string;
  cost_sea_profit?: number | null;
  cost_sea_margin?: number | null;
  cost_basis?: "weight" | "per_piece" | null;
  // ASIN 维度补充字段（最新计算结果 + 库存快照）
  calc_date?: string | null;
  calc_score?: number | null;
  calc_base_score?: number | null;
  calc_level?: string | null;
  calc_qty?: number | null;
  calc_inventory_days?: number | null;
  calc_cycle?: number | null;
  calc_trigger?: string | null;
  inv_snapshot_date?: string | null;
  inv_fba_available?: number | null;
  inv_inbound?: number | null;
  inv_available_days?: number | null;
  inv_estimated_daily_sales?: number | null;
}

export interface ProductPage {
  total: number;
  items: Product[];
}

export interface AsinCompareResult {
  total: number;
  asins: string[];
  matched: number;
  missing: number;
  missing_asins: string[];
  sync_triggered: boolean;
}

export interface ExcludeAsinsResult {
  total: number;
  excluded_count: number;
  kept_count: number;
  exclude_list_count: number;
  marked_stopped: number;
  sync_triggered: boolean;
}

export interface KeepAsinsResult {
  total: number;
  removed_from_exclude: number;
  keep_list_count: number;
  exclude_list_count: number;
  restored: number;
  sync_triggered: boolean;
}

/** ASIN 列表页条目（保留/排除列表中库内不存在的 ASIN：in_db=false） */
export interface AsinListItem {
  asin: string;
  product_name: string | null;
  category: string | null;
  operator: string | null;
  life_cycle: string | null;
  product_level: string | null;
  status: boolean | null;
  status_text: string | null;
  in_db: boolean;
}

export type AsinListTab = "available" | "all" | "keep" | "exclude";

export interface AsinListPage {
  total: number;
  items: AsinListItem[];
  /** ASIN 列表最近一次手动操作时间 */
  list_updated_at: string;
  /** 最近一次产品导入完成时间（sync_logs） */
  last_import_at: string;
}

export interface AsinListActionResult {
  action: string;
  asins: string[];
  status_changed: number;
  exclude_list_count: number;
  keep_list_count: number;
}

export interface ProductCostOverride {
  price?: number | null;
  cost_cny?: number | null;
  exchange_rate?: number | null;
  length_cm?: number | null;
  width_cm?: number | null;
  height_cm?: number | null;
  weight_kg?: number | null;
  freight_sea_cny?: number | null;
  freight_air_cny?: number | null;
  freight_express_cny?: number | null;
  sorting_fee?: number | null;
  referral_fee?: number | null;
  packing_fee?: number | null;
  inbound_fee?: number | null;
  storage_fee?: number | null;
  ad_fee?: number | null;
  return_loss?: number | null;
  over_threshold_loss?: number | null;
  misc_fee?: number | null;
  notes?: string | null;
}

export interface CostImportResult {
  message: string;
  matched: number;
  updated: number;
  skipped: number;
  errors: string[];
}

export interface CalculationResult {
  id: number;
  asin: string;
  operator?: string | null;
  primary_operator?: string | null;
  product_name?: string | null;
  product_stage?: string | null;
  product_level?: string | null;
  life_cycle?: string | null;
  calc_date: string;
  forecast_total: number | null;
  forecast_months?: { month: string; forecast_qty: number; seasonal_factor?: number }[] | null;
  available_stock: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  urgency_score: number | null;
  purchase_trigger: string | null;
  suggested_qty: number | null;
  batch_plan: string | null;
  batch_plan_text?: string | null;
  purchase_score: number | null;
  base_score?: number | null;
  purchase_level: string | null;
  score_detail: string | null;
  score_detail_text?: string | null;
  sales_trend?: {
    last7: number;
    prev7: number;
    change_percent: number | null;
    direction: string;
    text: string;
  } | null;
  ai_analysis?: string | null;
  price?: number | null;
  cost_price?: number | null;
  cost_price_cny?: number | null;
  seven_spend?: number | null;
  thirty_spend?: number | null;
  thirty_amount?: number | null;
  ad_spend_ratio?: number | null;
  acos_30d?: number | null;
  profit_rate?: number | null;
  est_profit_rate?: number | null;
  cost_table?: CostTable | null;
  // 结果采纳（运营确认该日/该ASIN 分析正确，用于准确率统计）
  adopted?: boolean | null;
  adopted_at?: string | null;
  adopted_by?: string | null;
  adopted_confidence?: number | null;
}

export interface DailyAlert {
  asin: string;
  product_name: string | null;
  operator: string | null;
  primary_operator: string | null;
  operators: string[];
  feishu_user_ids: (string | null)[];
  alert_type?: string | null;
  alert_reason?: string | null;
  purchase_level: string | null;
  purchase_score: number | null;
  base_score?: number | null;
  suggested_qty: number;
  inventory_days: number | null;
  available_stock: number | null;
  purchase_trigger?: string | null;
  ai_analysis?: string | null;
}

export interface DailyReport {
  calc_date: string;
  data_date?: string;
  total_asins: number;
  immediate_count: number;
  observe_count: number;
  pause_count: number;
  stockout_count?: number;
  pause_stockout_count?: number;
  ai_summary?: string;
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

export interface FestivalCalendarPayload {
  festival: string;
  festival_en?: string | null;
  listing_start?: string | null;
  festival_date?: string | null;
  festival_end?: string | null;
  hot_period?: string | null;
  hot_start_month?: number | null;
  hot_end_month?: number | null;
  launch_stage?: string | null;
  growth_stage?: string | null;
  mature_stage?: string | null;
  decline_stage?: string | null;
  notes?: string | null;
}

export interface LifecycleApplyResult {
  total: number;
  matched: number;
  updated: number;
  fallback: number;
  fallback_asins: string[];
  by_lifecycle: Record<string, number>;
}

export interface LifecycleDistribution {
  total: number;
  by_lifecycle: Record<string, number>;
  fallback: number;
}

export interface CategoryLeadtime {
  id: number;
  level1_category: string;
  level2_category: string | null;
  lead_time_min: number | null;
  lead_time_max: number | null;
  notes: string | null;
}

export interface DataSourceDict {
  id: number;
  table_name: string;
  field_name: string;
  field_comment: string | null;
  data_source: string | null;
  collect_method: string | null;
  update_freq: string | null;
  notes: string | null;
  category: string | null;
  sort_order: number | null;
  used_count: number | null;
  is_used: boolean | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DataSourceScanResult {
  total_scan: number;
  added: number;
  updated: number;
  skipped: number;
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

export interface SyncTaskStat {
  sync_type: string;
  label: string;
  status: string | null;
  total_count: number | null;
  success_count: number | null;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
}

export interface SyncSourceStat {
  key: string;
  label: string;
  count: number;
  status: "ok" | "warn" | "none";
  detail: string | null;
}

export interface SyncOverview {
  date: string;
  tasks: SyncTaskStat[];
  sources: SyncSourceStat[];
}

export interface HealthStatus {
  status: string;
  env?: string;
  database?: string;
}

export interface AppInfo {
  name: string;
  version: string;
  env: string;
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

export interface CalcJob {
  job_id: string;
  status: "running" | "done" | "failed";
  started_at: number | null;
  finished_at: number | null;
  stats: BatchStats | null;
  error: string | null;
  progress?: {
    total: number;
    done: number;
    percent: number;
    current_asin: string | null;
  };
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

export interface NextCalculation {
  next_date: string | null;
  next_levels: string[];
  next_levels_text: string;
  due_count: number;
  today: string;
}

export interface HistoryItem {
  calc_date: string;
  purchase_score: number | null;
  base_score?: number | null;
  suggested_qty: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  purchase_level: string | null;
  purchase_trigger: string | null;
}

export interface CostChannel {
  label: string;
  freight_fee: number;
  freight_fee_usd: number;
  mc?: number;
  packing_card?: number;
  sorting_fee?: number;
  referral_fee?: number;
  inbound_fee?: number;
  storage_fee?: number;
  ad_fee?: number;
  return_loss?: number;
  over_threshold_loss?: number;
  misc_fee?: number;
  profit: number;
  margin: number | null;
  profitable: boolean;
}

export interface CostTable {
  asin?: string;
  product_name?: string;
  price: number;
  is_peak: boolean;
  unit_cost: number;
  exchange_rate?: number;
  dims_cm?: number[];
  freight_basis?: "weight" | "per_piece";
  weight_kg?: number;
  cost_price_cny?: number | null;
  fba_fee: number;
  referral_fee: number;
  channels: Record<string, CostChannel>;
  profitable_modes: string[];
  all_profitable: boolean;
  conclusion_ok: boolean;
  conclusion: string;
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

export interface OverviewItem {
  asin: string;
  product_name: string;
  operator: string | null;
  primary_operator: string | null;
  product_level: string | null;
  life_cycle: string | null;
  product_stage: string | null;
  product_type: string | null;
  calc_date: string | null;
  purchase_score: number | null;
  base_score?: number | null;
  purchase_level: string | null;
  suggested_qty: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  available_stock: number | null;
  urgency_score: number | null;
  purchase_trigger: string | null;
  user_feedback: string | null;
  feedback_at: string | null;
  // 结果采纳（运营确认该日/该ASIN 分析正确，用于准确率统计）
  adopted?: boolean | null;
  adopted_at?: string | null;
  adopted_by?: string | null;
  adopted_confidence?: number | null;
}

export interface OverviewResponse {
  total: number;
  items: OverviewItem[];
}

export interface InventoryHealthItem {
  asin: string;
  product_name: string;
  operator: string | null;
  primary_operator: string | null;
  product_level: string | null;
  life_cycle: string | null;
  product_type: string | null;
  calc_date: string | null;
  forecast_total: number | null;
  available_stock: number | null;
  inventory_days: number | null;
  replenishment_cycle: number | null;
  urgency_score: number | null;
  urgency_level: string | null;
  purchase_trigger: string | null;
  suggested_qty: number | null;
  batch_plan: string | null;
  purchase_score: number | null;
  base_score?: number | null;
  purchase_level: string | null;
  score_detail: string | null;
}

export interface InventoryHealthResponse {
  total: number;
  items: InventoryHealthItem[];
  thresholds: { danger: number; low: number; healthy: number };
}

export interface Operator {
  id: number;
  name: string;
  role: string | null;
  status: boolean;
  feishu_user_id: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
}

export interface AiEvaluationResult {
  status: string;
  asin: string;
  conclusion?: string | null;
  suggested_qty?: number | null;
  reason?: string | null;
  risks?: string[];
  confidence?: string | null;
  error?: string | null;
  model?: string | null;
}

export interface ConfigParamItem {
  key: string;
  value: string | number;
  default: string | number;
  description: string;
  type: string;
  is_override: boolean;
}

export interface AnalysisItem {
  asin: string;
  name: string;
  life_cycle: string | null;
  product_type: string | null;
  festival: string | null;
  price: string | number | null;
  cost_price?: number | null;
  cost_price_cny?: number | null;
  seven_spend?: number | null;
  thirty_spend?: number | null;
  ad_spend_ratio?: number | null;
  acos_30d?: number | null;
  profit_rate?: number | null;
  est_profit_rate?: number | null;
  cost_table?: CostTable | null;
  box_qty: number | null;
  calc_date: string | null;
  score: number | null;
  calc_level: string | null;
  suggested_qty: number | null;
  inv_days: number | null;
  replenish_cycle: number | null;
  trigger: string | null;
  vol30: number | null;
  yesterday_vol: number | null;
  avail: number | null;
}

export interface AnalysisSummary {
  level: string;
  snapshot_date: string | null;
  inventory_date: string | null;
  total: number;
  overview: {
    vol30_total: number;
    vol30_avg: number;
    scored_count: number;
    avg_score: number | null;
    stockout_risk: number;
    days0: number;
    overstock: number;
  };
  distributions: {
    life_cycle: Record<string, number>;
    product_type: Record<string, number>;
    festival: Record<string, number>;
    calc_level: Record<string, number>;
  };
  top_sales: AnalysisItem[];
  risks: { stockout: AnalysisItem[]; overstock: AnalysisItem[] };
  items: AnalysisItem[];
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

async function fetchForm<T>(path: string, file: File): Promise<T> {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch(path, { method: "POST", body: fd });
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  return res.json();
}

/** 下载后端生成的文件（CSV/TXT），触发浏览器保存 */
async function downloadFile(
  path: string,
  filename: string,
  params?: Record<string, string | number | boolean | undefined | null>
): Promise<void> {
  const res = await fetch(`${path}${toQuery(params)}`);
  if (!res.ok) {
    let message = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      message = body.detail || message;
    } catch {
      /* ignore */
    }
    throw new Error(message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export const api = {
  health: () => fetchJSON<HealthStatus>("/health"),
  appInfo: () => fetchJSON<AppInfo>("/api/v1/app-info"),
  products: {
    list: (params?: { skip?: number; limit?: number; keyword?: string; status?: boolean; life_cycle?: string; product_level?: string; category?: string; product_type?: string; operator?: string; filters?: string }) =>
      fetchJSON<ProductPage>(`/api/v1/products${toQuery({ limit: 50, ...params })}`),
    get: (asin: string) => fetchJSON<Product>(`/api/v1/products/${asin}`),
    update: (asin: string, data: Partial<Pick<Product, "operator" | "acos_30d">>) =>
      fetchJSON<Product>(`/api/v1/products/${asin}`, { method: "PUT", body: JSON.stringify(data) }),
    uploadAsins: (file: File) => fetchForm<AsinCompareResult>("/api/v1/products/upload-asins", file),
    compareAsins: (asins: string[]) =>
      fetchJSON<AsinCompareResult>("/api/v1/products/compare-asins", { method: "POST", body: JSON.stringify({ asins }) }),
    lifecycleStats: () => fetchJSON<LifecycleStats>("/api/v1/products/stats/lifecycle"),
    asinList: (params?: { tab?: AsinListTab; keyword?: string; skip?: number; limit?: number }) =>
      fetchJSON<AsinListPage>(`/api/v1/products/asin-list${toQuery({ limit: 50, ...params })}`),
    asinListAction: (action: "exclude" | "keep" | "remove_exclude" | "remove_keep", asins: string[]) =>
      fetchJSON<AsinListActionResult>("/api/v1/products/asin-lists/action", {
        method: "POST",
        body: JSON.stringify({ action, asins }),
      }),
    exportAsins: (params?: {
      keyword?: string;
      status?: boolean;
      life_cycle?: string;
      product_level?: string;
      category?: string;
      product_type?: string;
      operator?: string;
      filters?: string;
      fields?: string[];
    }) => {
      const { fields, ...rest } = params ?? {};
      return downloadFile("/api/v1/products/export-asins", `asins_${new Date().toISOString().slice(0, 10)}.csv`, {
        ...rest,
        fields: fields && fields.length ? fields.join(",") : undefined,
      });
    },
    exportExcludeAsins: () =>
      downloadFile("/api/v1/products/export-exclude-asins", `exclude_asins_${new Date().toISOString().slice(0, 10)}.txt`),
    excludeAsins: (file: File, triggerSync = true) =>
      fetchForm<ExcludeAsinsResult>(`/api/v1/products/exclude-asins${triggerSync ? "?trigger_sync=true" : "?trigger_sync=false"}`, file),
    keepAsins: (file: File, triggerSync = true) =>
      fetchForm<KeepAsinsResult>(`/api/v1/products/keep-asins${triggerSync ? "?trigger_sync=true" : "?trigger_sync=false"}`, file),
    cost: {
      get: (asin: string) => fetchJSON<{ asin: string; overrides: ProductCostOverride; table: CostTable }>(`/api/v1/products/${asin}/cost`),
      update: (asin: string, data: ProductCostOverride) =>
        fetchJSON<{ asin: string; message: string; overrides: ProductCostOverride }>(`/api/v1/products/${asin}/cost`, {
          method: "PUT",
          body: JSON.stringify(data),
        }),
      import: (file: File) => fetchForm<CostImportResult>("/api/v1/products/cost/import", file),
    },
  },
  operators: {
    list: (params?: { keyword?: string; status?: boolean; skip?: number; limit?: number }) =>
      fetchJSON<Operator[]>(`/api/v1/operators${toQuery({ limit: 200, ...params })}`),
    distinctNames: () => fetchJSON<{ names: string[] }>("/api/v1/operators/distinct-names"),
    sync: () => fetchJSON<{ message: string; total: number; created: number; enabled: number }>("/api/v1/operators/sync", { method: "POST" }),
    create: (data: { name: string; role?: string; feishu_user_id?: string; status?: boolean; notes?: string }) =>
      fetchJSON<Operator>("/api/v1/operators/", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<{ name: string; role: string; feishu_user_id: string; status: boolean; notes: string }>) =>
      fetchJSON<Operator>(`/api/v1/operators/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    remove: (id: number) => fetchJSON<{ message: string }>(`/api/v1/operators/${id}`, { method: "DELETE" }),
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
      fetchJSON<{ message: string; data: CalculationResult; notify: { sent: boolean; reason?: string; operator?: string } }>(
        `/api/v1/calculation/trigger/${asin}`,
        { method: "POST" }
      ),
    triggerBatch: () =>
      fetchJSON<{ message: string; job_id: string; status: string }>("/api/v1/calculation/trigger/batch", { method: "POST" }),
    dueStats: () => fetchJSON<DueStats>("/api/v1/calculation/due-stats"),
    triggerDue: () =>
      fetchJSON<{ message: string; job_id: string; status: string }>("/api/v1/calculation/trigger/due", { method: "POST" }),
    nextCalculation: () => fetchJSON<NextCalculation>("/api/v1/calculation/next-calculation"),
    triggerLevel: (level: string) =>
      fetchJSON<{ message: string; job_id: string; status: string }>(`/api/v1/calculation/trigger/level/${level}`, { method: "POST" }),
    getJob: (jobId: string) => fetchJSON<CalcJob>(`/api/v1/calculation/jobs/${jobId}`),
    overview: (params?: { keyword?: string; operator?: string; product_level?: string; life_cycle?: string; purchase_level?: string; calc_date?: string; skip?: number; limit?: number }) =>
      fetchJSON<OverviewResponse>(`/api/v1/calculation/results/overview${toQuery({ limit: 200, ...params })}`),
    inventoryHealth: (params?: {
      keyword?: string;
      operator?: string;
      urgency?: string;
      purchase_level?: string;
      skip?: number;
      limit?: number;
    }) => fetchJSON<InventoryHealthResponse>(`/api/v1/calculation/inventory-health${toQuery({ limit: 200, ...params })}`),
    history: (asin: string) => fetchJSON<HistoryItem[]>(`/api/v1/calculation/results/${asin}/history`),
    feedback: (payload: { asin: string; calc_date?: string; feedback: string; operator?: string }) =>
      fetchJSON<{ asin: string; calc_date: string; user_feedback: string; feedback_at: string }>(
        "/api/v1/calculation/results/feedback",
        { method: "POST", body: JSON.stringify(payload) }
      ),
    adopt: (payload: { asin: string; calc_date?: string; operator?: string; adopted?: boolean; confidence?: number }) =>
      fetchJSON<{ asin: string; calc_date: string; adopted: boolean; adopted_at: string | null; adopted_by: string | null; adopted_confidence: number | null }>(
        "/api/v1/calculation/results/adopt",
        { method: "POST", body: JSON.stringify(payload) }
      ),
    risks: () => fetchJSON<RiskReport>("/api/v1/calculation/risks"),
    costTable: (asin: string, price?: number) =>
      fetchJSON<CostTable>(`/api/v1/calculation/cost-table/${asin}${price != null ? `?price=${price}` : ""}`),
    dailyReport: (includeResults = true) =>
      fetchJSON<DailyReport>(`/api/v1/calculation/daily-report${includeResults ? "" : "?include_results=false"}`),
    pushReport: () =>
      fetchJSON<{ message: string; summary: { total_asins: number; immediate_count: number; observe_count: number; pause_count: number } }>(
        "/api/v1/calculation/daily-report/push",
        { method: "POST" }
      ),
  },
  festivalCalendar: {
    list: () => fetchJSON<FestivalCalendar[]>("/api/v1/festival-calendar?limit=200"),
    create: (data: FestivalCalendarPayload) =>
      fetchJSON<FestivalCalendar>("/api/v1/festival-calendar", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<FestivalCalendarPayload>) =>
      fetchJSON<FestivalCalendar>(`/api/v1/festival-calendar/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    remove: (id: number) => fetchJSON<{ deleted: number }>(`/api/v1/festival-calendar/${id}`, { method: "DELETE" }),
    importFile: (file: File) =>
      fetchForm<{ imported: number; festivals: string[] }>("/api/v1/festival-calendar/import", file),
    downloadTemplate: () => downloadFile("/api/v1/festival-calendar/template", "festival_lifecycle_template.xlsx"),
    applyLifecycle: (grades = "S,A") =>
      fetchJSON<LifecycleApplyResult>(`/api/v1/festival-calendar/apply-lifecycle?grades=${grades}`, { method: "POST" }),
    lifecycleDistribution: (grades = "S,A") =>
      fetchJSON<LifecycleDistribution>(`/api/v1/festival-calendar/lifecycle-distribution?grades=${grades}`),
  },
  categoryLeadtimes: {
    list: () => fetchJSON<CategoryLeadtime[]>("/api/v1/category-leadtimes?limit=200"),
    create: (data: Partial<Pick<CategoryLeadtime, "level1_category" | "level2_category" | "lead_time_min" | "lead_time_max" | "notes">>) =>
      fetchJSON<CategoryLeadtime>("/api/v1/category-leadtimes", { method: "POST", body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Pick<CategoryLeadtime, "level1_category" | "level2_category" | "lead_time_min" | "lead_time_max" | "notes">>) =>
      fetchJSON<CategoryLeadtime>(`/api/v1/category-leadtimes/${id}`, { method: "PUT", body: JSON.stringify(data) }),
    remove: (id: number) => fetchJSON<{ message: string }>(`/api/v1/category-leadtimes/${id}`, { method: "DELETE" }),
  },
  dataSource: {
    list: (params?: { category?: string; q?: string; skip?: number; limit?: number }) =>
      fetchJSON<DataSourceDict[]>(`/api/v1/data-source${toQuery({ limit: 2000, ...params })}`),
    categories: () => fetchJSON<string[]>("/api/v1/data-source/categories"),
    scan: () => fetchJSON<DataSourceScanResult>("/api/v1/data-source/scan", { method: "POST" }),
    batchUpdate: (items: Array<Partial<DataSourceDict> & { id: number }>) =>
      fetchJSON<DataSourceDict[]>("/api/v1/data-source/batch", { method: "PUT", body: JSON.stringify(items) }),
    create: (data: Partial<DataSourceDict>) =>
      fetchJSON<DataSourceDict>("/api/v1/data-source", { method: "POST", body: JSON.stringify(data) }),
    remove: (id: number) => fetchJSON<{ message: string }>(`/api/v1/data-source/${id}`, { method: "DELETE" }),
  },
  syncLogs: {
    list: () => fetchJSON<SyncLog[]>("/api/v1/sync-logs?limit=50"),
    run: (syncType: string) =>
      fetchJSON<{ message: string; sync_type: string }>(`/api/v1/sync-logs/run?sync_type=${syncType}`, { method: "POST" }),
  },
  syncOverview: () => fetchJSON<SyncOverview>("/api/v1/sync-overview"),
  ai: {
    evaluate: (asin: string) =>
      fetchJSON<AiEvaluationResult>(`/api/v1/ai/evaluate/${asin}`, { method: "POST" }),
    dailyReport: () =>
      fetchJSON<DailyReport>("/api/v1/ai/daily-report", { method: "POST" }),
    latest: (asin: string) =>
      fetchJSON<AiEvaluationResult & { status: string }>(`/api/v1/ai/latest/${asin}`),
  },
  config: {
    list: () => fetchJSON<ConfigParamItem[]>("/api/v1/config"),
    update: (key: string, value: string) =>
      fetchJSON<{ key: string; value: string | number }>(`/api/v1/config/${key}`, {
        method: "PUT",
        body: JSON.stringify({ value }),
      }),
  },
  analysis: {
    summary: (level: string) => fetchJSON<AnalysisSummary>(`/api/v1/analysis/summary?level=${level}`),
  },
};

/** 轮询后台计算任务直到完成 */
export async function pollJob(jobId: string, intervalMs = 2000, timeoutMs = 900000): Promise<CalcJob> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await api.calculation.getJob(jobId);
    if (job.status !== "running") return job;
    if (Date.now() > deadline) throw new Error("任务超时");
    await new Promise(r => setTimeout(r, intervalMs));
  }
}
