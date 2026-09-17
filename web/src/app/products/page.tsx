"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type AsinCompareResult, type Product, type ProductPage } from "@/lib/api";
import DateRangeFilter from "@/components/DateRangeFilter";

// 等级徽标样式（S爆款→A→B→C→D）
const LEVEL_STYLE: Record<string, { bg: string; color: string }> = {
  S: { bg: "#fef3c7", color: "#92400e" },
  A: { bg: "#dbeafe", color: "#1e40af" },
  B: { bg: "#dcfce7", color: "#166534" },
  C: { bg: "#f1f5f9", color: "#334155" },
  D: { bg: "#f1f5f9", color: "#64748b" },
};

interface ColumnDef {
  key: keyof Product;
  label: string;
  defaultVisible: boolean;
  align?: "left" | "right" | "center";
}

const COLUMNS: ColumnDef[] = [
  { key: "asin", label: "ASIN", defaultVisible: true },
  { key: "product_name", label: "品名", defaultVisible: true },
  { key: "msku", label: "MSKU", defaultVisible: true },
  { key: "category", label: "分类", defaultVisible: true },
  { key: "brand", label: "品牌", defaultVisible: true },
  { key: "price", label: "售价", defaultVisible: true, align: "right" },
  { key: "thirty_volume", label: "30天销量", defaultVisible: true, align: "right" },
  { key: "average_thirty_volume", label: "30天日均", defaultVisible: true, align: "right" },
  { key: "afn_fulfillable_quantity", label: "FBA可售", defaultVisible: true, align: "right" },
  { key: "afn_inbound_shipped_quantity", label: "在途", defaultVisible: true, align: "right" },
  { key: "rank", label: "大类排名", defaultVisible: true, align: "right" },
  { key: "stars", label: "评分", defaultVisible: true, align: "right" },
  { key: "reviews_num", label: "评论数", defaultVisible: true, align: "right" },
  { key: "list_date", label: "上架日期", defaultVisible: true },
  { key: "primary_operator", label: "负责人", defaultVisible: true },
  { key: "operator", label: "全部负责人", defaultVisible: false },
  { key: "product_level", label: "等级", defaultVisible: true },
  { key: "life_cycle", label: "生命周期", defaultVisible: true },
  { key: "status", label: "状态", defaultVisible: true },
  // ── 身份标识 ──
  { key: "local_sku", label: "本地SKU", defaultVisible: false },
  { key: "fnsku", label: "FNSKU", defaultVisible: false },
  { key: "parent_asin", label: "父体ASIN", defaultVisible: false },
  { key: "product_relation_id", label: "款名/SPU", defaultVisible: false },
  { key: "amz_product_id", label: "亚马逊商品ID", defaultVisible: false },
  { key: "lx_id", label: "领星ID", defaultVisible: false, align: "right" },
  { key: "store_id", label: "店铺ID", defaultVisible: false, align: "right" },
  // ── 名称与描述 ──
  { key: "listing_title", label: "亚马逊标题", defaultVisible: false },
  { key: "local_name", label: "内部品名", defaultVisible: false },
  { key: "amz_product_type", label: "亚马逊产品类型", defaultVisible: false },
  { key: "model", label: "型号", defaultVisible: false },
  { key: "remark", label: "备注", defaultVisible: false },
  // ── 价格与费用 ──
  { key: "listing_price", label: "Listing价", defaultVisible: false, align: "right" },
  { key: "landed_price", label: "落地价", defaultVisible: false, align: "right" },
  { key: "regular_price", label: "日常价", defaultVisible: false, align: "right" },
  { key: "list_price", label: "目录价", defaultVisible: false, align: "right" },
  { key: "b2b_price", label: "B2B价", defaultVisible: false, align: "right" },
  { key: "fba_fee", label: "FBA费用", defaultVisible: false, align: "right" },
  { key: "referral_fee", label: "佣金", defaultVisible: false, align: "right" },
  { key: "shipping", label: "运费", defaultVisible: false, align: "right" },
  { key: "cost_price", label: "采购报价", defaultVisible: false, align: "right" },
  { key: "history_price", label: "历史价格", defaultVisible: false, align: "right" },
  // ── 销量与销售额 ──
  { key: "total_volume", label: "历史总销量", defaultVisible: false, align: "right" },
  { key: "yesterday_volume", label: "昨日销量", defaultVisible: false, align: "right" },
  { key: "seven_volume", label: "7天销量", defaultVisible: false, align: "right" },
  { key: "fourteen_volume", label: "14天销量", defaultVisible: false, align: "right" },
  { key: "average_seven_volume", label: "7天日均", defaultVisible: false, align: "right" },
  { key: "average_fourteen_volume", label: "14天日均", defaultVisible: false, align: "right" },
  { key: "yesterday_amount", label: "昨日销售额", defaultVisible: false, align: "right" },
  { key: "seven_amount", label: "7天销售额", defaultVisible: false, align: "right" },
  { key: "fourteen_amount", label: "14天销售额", defaultVisible: false, align: "right" },
  { key: "thirty_amount", label: "30天销售额", defaultVisible: false, align: "right" },
  // ── 广告花费 ──
  { key: "yesterday_spend", label: "昨日广告", defaultVisible: false, align: "right" },
  { key: "seven_spend", label: "7天广告", defaultVisible: false, align: "right" },
  { key: "fourteen_spend", label: "14天广告", defaultVisible: false, align: "right" },
  { key: "thirty_spend", label: "30天广告", defaultVisible: false, align: "right" },
  // ── 库存（FBA） ──
  { key: "afn_reserved_quantity", label: "FBA预留", defaultVisible: false, align: "right" },
  { key: "reserved_fc_transfers", label: "待调仓", defaultVisible: false, align: "right" },
  { key: "reserved_customerorders", label: "客户订单预留", defaultVisible: false, align: "right" },
  { key: "afn_unsellable_quantity", label: "不可售", defaultVisible: false, align: "right" },
  { key: "afn_inbound_working_quantity", label: "入库中", defaultVisible: false, align: "right" },
  { key: "afn_inbound_receiving_quantity", label: "待发货", defaultVisible: false, align: "right" },
  // ── 排名与表现 ──
  { key: "seller_rank", label: "卖家排名", defaultVisible: false, align: "right" },
  { key: "category_rank", label: "类目排名", defaultVisible: false },
  { key: "small_rank", label: "小类排名", defaultVisible: false },
  { key: "seller_category", label: "卖家类目", defaultVisible: false },
  // ── 时间字段 ──
  { key: "open_date_time", label: "上架时间", defaultVisible: false },
  { key: "first_order_time", label: "首单时间", defaultVisible: false },
  { key: "on_sale_time", label: "开售时间", defaultVisible: false },
  { key: "create_time", label: "创建时间", defaultVisible: false },
  { key: "update_time", label: "更新时间", defaultVisible: false },
  // ── 分类与品牌 ──
  { key: "brand_name", label: "品牌名", defaultVisible: false },
  { key: "category_id", label: "分类ID", defaultVisible: false, align: "right" },
  // ── 店铺与运营 ──
  { key: "shop", label: "店铺", defaultVisible: false },
  { key: "marketplace", label: "站点", defaultVisible: false },
  { key: "seller_name", label: "卖家名称", defaultVisible: false },
  { key: "fulfillment_channel_type", label: "配送渠道", defaultVisible: false },
  { key: "status_text", label: "状态文本", defaultVisible: false },
  { key: "product_creator_realname", label: "创建人", defaultVisible: false },
  { key: "product_developer", label: "开发人", defaultVisible: false },
  // ── 标签与采购档案 ──
  { key: "tags", label: "标签", defaultVisible: true },
  { key: "supplier_name", label: "供应商", defaultVisible: false },
  { key: "lead_time", label: "工期(天)", defaultVisible: false, align: "right" },
  { key: "box_quantity", label: "单箱数量", defaultVisible: false, align: "right" },
  { key: "min_order_qty", label: "最低采购", defaultVisible: false, align: "right" },
  // ── 业务字段 ──
  { key: "product_type", label: "产品类型", defaultVisible: false },
  { key: "calc_frequency", label: "计算频率", defaultVisible: false },
  { key: "product_stage", label: "阶段", defaultVisible: false },
  { key: "festival", label: "节日", defaultVisible: true },
  { key: "core_months", label: "核心月份", defaultVisible: false },
  { key: "profit_rate", label: "利润率", defaultVisible: false, align: "right" },
  { key: "cost_sea_profit", label: "海运利润$", defaultVisible: true, align: "right" },
  { key: "cost_sea_margin", label: "海运毛利率", defaultVisible: true, align: "right" },
  // ── ASIN 维度补充（最新计算 + 库存快照） ──
  { key: "calc_score", label: "最新评分", defaultVisible: false, align: "right" },
  { key: "calc_base_score", label: "原始分", defaultVisible: false, align: "right" },
  { key: "calc_level", label: "决策等级", defaultVisible: false },
  { key: "calc_qty", label: "建议量", defaultVisible: false, align: "right" },
  { key: "calc_inventory_days", label: "库存天数", defaultVisible: false, align: "right" },
  { key: "calc_cycle", label: "补货周期", defaultVisible: false, align: "right" },
  { key: "calc_trigger", label: "采购触发", defaultVisible: false },
  { key: "calc_date", label: "计算日期", defaultVisible: false },
  { key: "inv_snapshot_date", label: "库存快照日期", defaultVisible: false },
  { key: "inv_fba_available", label: "快照FBA可售", defaultVisible: false, align: "right" },
  { key: "inv_inbound", label: "快照在途", defaultVisible: false, align: "right" },
  { key: "inv_available_days", label: "领星可售天数", defaultVisible: false, align: "right" },
  { key: "inv_estimated_daily_sales", label: "预估日销", defaultVisible: false, align: "right" },
  { key: "created_at", label: "创建时间", defaultVisible: false },
  { key: "updated_at", label: "更新时间", defaultVisible: false },
];

const STORAGE_KEY = "products-column-visibility";
const PAGE_SIZES = [20, 30, 50, 100, 200];
const LEVELS = ["S", "A", "B", "C", "D"];
const LIFECYCLES = ["启动期", "增长期", "热卖期", "成熟期", "下降期"];
const TYPES = ["节日产品", "长期产品", "未分类"];

// ── 列筛选（筛选显隐） ──────────────────────────────
type FilterVal =
  | { op: "like"; value: string }
  | { op: "eq"; value: string }
  | { op: "range"; min?: string; max?: string };

const FILTER_STORAGE_KEY = "products-filter-visibility";
// 下拉筛选的列（固定选项）
const FILTER_SELECT_FIELDS = ["product_level", "life_cycle", "product_type", "product_stage"];
const FILTER_OPTIONS: Record<string, { label: string; value: string }[]> = {
  product_level: LEVELS.map(l => ({ label: `${l} 级`, value: l })),
  life_cycle: LIFECYCLES.map(l => ({ label: l, value: l })),
  product_type: TYPES.map(t => ({ label: t, value: t })),
  product_stage: ["新品", "老品"].map(v => ({ label: v, value: v })),
};
// 数值范围筛选的列
const FILTER_NUMERIC_KEYS = new Set([
  "price", "listing_price", "landed_price", "regular_price", "list_price", "b2b_price",
  "fba_fee", "referral_fee", "shipping", "history_price",
  "yesterday_amount", "seven_amount", "fourteen_amount", "thirty_amount",
  "yesterday_spend", "seven_spend", "fourteen_spend", "thirty_spend",
  "cost_sea_profit", "cost_price", "cost_sea_margin", "profit_rate",
  "thirty_volume", "average_thirty_volume", "average_seven_volume", "average_fourteen_volume",
  "afn_fulfillable_quantity", "afn_reserved_quantity", "reserved_fc_transfers", "reserved_fc_processing",
  "reserved_customerorders", "afn_inbound_shipped_quantity", "afn_unsellable_quantity",
  "afn_inbound_working_quantity", "afn_inbound_receiving_quantity", "quantity",
  "rank", "seller_rank", "category_rank", "small_rank", "reviews_num", "stars",
  "total_volume", "yesterday_volume", "seven_volume", "fourteen_volume",
  "lx_id", "store_id", "category_id", "brand_id", "lead_time", "box_quantity", "min_order_qty",
  "calc_score", "calc_base_score", "calc_qty", "calc_inventory_days", "calc_cycle",
  "inv_fba_available", "inv_inbound", "inv_available_days", "inv_estimated_daily_sales",
]);
// 日期范围筛选的列
const FILTER_DATE_KEYS = new Set([
  "list_date", "open_date_time", "first_order_time", "on_sale_time", "create_time", "update_time",
  "calc_date", "inv_snapshot_date", "created_at", "updated_at",
]);
// 默认显示筛选按钮的列（原顶部快速筛选，保留同等能力）
const FILTER_DEFAULT_ON = ["calc_date", "product_level", "life_cycle", "product_type", "primary_operator", "status"];

function filterTypeOf(key: keyof Product): "select" | "range" | "date" | "text" {
  if (FILTER_SELECT_FIELDS.includes(key as string)) return "select";
  if (FILTER_NUMERIC_KEYS.has(key)) return "range";
  if (FILTER_DATE_KEYS.has(key)) return "date";
  return "text";
}

function loadFilterVisibility(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(FILTER_STORAGE_KEY);
    if (raw) {
      const saved = JSON.parse(raw) as Record<string, boolean>;
      // 旧记录里缺失的新增默认列补为显示，避免默认筛选控件隐形生效
      const merged = { ...saved };
      FILTER_DEFAULT_ON.forEach(k => { if (!(k in merged)) merged[k] = true; });
      return merged;
    }
  } catch {
    /* ignore */
  }
  return Object.fromEntries(FILTER_DEFAULT_ON.map(k => [k, true]));
}

// 序列化列筛选为后端 JSON；status 列由 statusFilter 单独处理
function serializeFilters(fs: Record<string, FilterVal>): string | undefined {
  const entries = Object.entries(fs)
    .filter(([field]) => field !== "status")
    .filter(([, v]) => {
      if (v.op === "like" || v.op === "eq") return String(v.value ?? "").trim() !== "";
      return String(v.min ?? "") !== "" || String(v.max ?? "") !== "";
    })
    .map(([field, v]) => ({ field, ...v }));
  return entries.length ? JSON.stringify(entries) : undefined;
}

function loadVisibility(): Record<string, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
    /* ignore */
  }
  return Object.fromEntries(COLUMNS.map(c => [c.key, c.defaultVisible]));
}

// ── 列表状态持久化（点开 ASIN 详情后返回时恢复筛选/分页） ──
const STATE_STORAGE_KEY = "products-list-state";

/** 本地日期 YYYY-MM-DD */
function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 默认筛选条件：计算日期 = 今天 */
function defaultFilters(): Record<string, FilterVal> {
  const t = todayStr();
  return { calc_date: { op: "range", min: t, max: t } };
}

interface ListState {
  keyword: string;
  statusFilter: "active" | "deleted";
  filters: Record<string, FilterVal>;
  page: number;
  pageSize: number;
}

/** 从 sessionStorage 恢复列表状态；无记录时用默认值（计算日期=今天） */
function loadListState(): ListState {
  const fallback: ListState = {
    keyword: "",
    statusFilter: "active",
    filters: defaultFilters(),
    page: 0,
    pageSize: 50,
  };
  try {
    const raw = sessionStorage.getItem(STATE_STORAGE_KEY);
    if (!raw) return fallback;
    const p = JSON.parse(raw) as Partial<ListState>;
    return {
      keyword: typeof p.keyword === "string" ? p.keyword : "",
      statusFilter: p.statusFilter === "deleted" ? "deleted" : "active",
      filters: p.filters && typeof p.filters === "object" ? p.filters : defaultFilters(),
      page: Number.isInteger(p.page) && (p.page as number) >= 0 ? (p.page as number) : 0,
      pageSize: PAGE_SIZES.includes(p.pageSize as number) ? (p.pageSize as number) : 50,
    };
  } catch {
    return fallback;
  }
}

function cellValue(item: Product, key: keyof Product): string {
  const v = item[key];
  if (v === null || v === undefined || v === "") return "-";
  const usdKeys = new Set([
    "price", "listing_price", "landed_price", "regular_price", "list_price", "b2b_price",
    "fba_fee", "referral_fee", "shipping", "history_price",
    "yesterday_amount", "seven_amount", "fourteen_amount", "thirty_amount",
    "yesterday_spend", "seven_spend", "fourteen_spend", "thirty_spend",
    "cost_sea_profit",
  ]);
  if (key === "cost_price") return `￥${v}`;
  if (key === "cost_sea_margin") return `${(Number(v) * 100).toFixed(1)}%`;
  if (usdKeys.has(key)) return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  const numericKeys = new Set([
    "thirty_volume", "average_thirty_volume", "average_seven_volume", "average_fourteen_volume",
    "afn_fulfillable_quantity", "afn_reserved_quantity", "reserved_fc_transfers", "reserved_fc_processing",
    "reserved_customerorders", "afn_inbound_shipped_quantity", "afn_unsellable_quantity",
    "afn_inbound_working_quantity", "afn_inbound_receiving_quantity", "quantity",
    "rank", "seller_rank", "reviews_num", "total_volume", "yesterday_volume", "seven_volume",
    "fourteen_volume",
    "lx_id", "store_id", "category_id", "brand_id", "lead_time", "box_quantity", "min_order_qty",
    "calc_score", "calc_base_score", "calc_qty", "calc_inventory_days", "calc_cycle",
    "inv_fba_available", "inv_inbound", "inv_available_days", "inv_estimated_daily_sales",
  ]);
  if (numericKeys.has(key)) return Number(v).toLocaleString();
  if (key === "profit_rate") return `${(Number(v) * 100).toFixed(1)}%`;
  if (key === "list_date" || key === "calc_date" || key === "inv_snapshot_date") return String(v).slice(0, 10);
  if (key === "tags") {
    try {
      const arr = JSON.parse(String(v));
      if (Array.isArray(arr)) return arr.slice(0, 8).join("、");
    } catch { /* ignore */ }
  }
  return String(v);
}

export default function ProductsPage() {
  const router = useRouter();
  const [initial] = useState<ListState>(loadListState);
  const [data, setData] = useState<ProductPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState(initial.keyword);
  const [debounced, setDebounced] = useState(initial.keyword.trim());
  const [statusFilter, setStatusFilter] = useState<"active" | "deleted">(initial.statusFilter);
  const [operatorNames, setOperatorNames] = useState<string[]>([]);
  const [page, setPage] = useState(initial.page);
  const [pageSize, setPageSize] = useState(initial.pageSize);
  const [visibility, setVisibility] = useState<Record<string, boolean>>(loadVisibility);
  const [showColumns, setShowColumns] = useState(false);
  const [filterVisibility, setFilterVisibility] = useState<Record<string, boolean>>(loadFilterVisibility);
  const [showFilterPanel, setShowFilterPanel] = useState(false);
  const [filters, setFilters] = useState<Record<string, FilterVal>>(initial.filters);
  const [debouncedFilters, setDebouncedFilters] = useState<Record<string, FilterVal>>(initial.filters);
  const [asinList, setAsinList] = useState<string[]>([]);
  const [compareResult, setCompareResult] = useState<AsinCompareResult | null>(null);
  const [uploading, setUploading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [showMissing, setShowMissing] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [showExportFields, setShowExportFields] = useState(false);
  const [exportFields, setExportFields] = useState<string[]>([]);
  const [excludeUploading, setExcludeUploading] = useState(false);
  const [keepUploading, setKeepUploading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  // ── ASIN 排除/保留列表弹窗（实时增删改查，保存后经 config 接口生效） ──
  const [listEditor, setListEditor] = useState<{ key: string; label: string; asins: string[] } | null>(null);
  const [listInput, setListInput] = useState("");
  const [listSaving, setListSaving] = useState(false);
  const [configLoading, setConfigLoading] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => clearTimeout(t);
  }, [keyword]);

  // 列筛选防抖（文本输入 300ms 后再请求）
  useEffect(() => {
    const t = setTimeout(() => setDebouncedFilters(filters), 300);
    return () => clearTimeout(t);
  }, [filters]);

  const load = useCallback(() => {
    setLoading(true);
    api.products
      .list({
        skip: page * pageSize,
        limit: pageSize,
        keyword: debounced || undefined,
        filters: serializeFilters(debouncedFilters),
        status: statusFilter === "deleted" ? false : undefined,
      })
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [debounced, statusFilter, debouncedFilters, page, pageSize]);

  useEffect(() => { load(); }, [load]);

  // 列表状态写入 sessionStorage：点开 ASIN 详情返回后可恢复筛选与分页
  useEffect(() => {
    try {
      sessionStorage.setItem(
        STATE_STORAGE_KEY,
        JSON.stringify({ keyword, statusFilter, filters, page, pageSize }),
      );
    } catch {
      /* ignore */
    }
  }, [keyword, statusFilter, filters, page, pageSize]);

  // 恢复的分页超出当前结果范围时回退到最后一页
  useEffect(() => {
    if (!data) return;
    const tp = Math.max(1, Math.ceil(data.total / pageSize));
    if (page > tp - 1) setPage(tp - 1);
  }, [data, page, pageSize]);

  useEffect(() => {
    api.operators.distinctNames().then(r => setOperatorNames(r.names)).catch(() => setOperatorNames([]));
  }, []);

  const uploadFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setUploading(true);
    setUploadMsg(null);
    setCompareResult(null);
    try {
      const res = await api.products.uploadAsins(file);
      setAsinList(res.asins);
      setCompareResult(res);
      setUploadMsg({
        ok: true,
        text: `共 ${res.total} 个ASIN：匹配 ${res.matched}，缺失 ${res.missing}` +
          (res.sync_triggered ? "，已自动触发产品同步（稍等片刻后点「重新比对」）" : ""),
      });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "上传失败" });
    } finally {
      setUploading(false);
    }
  };

  const reCompare = async () => {
    if (asinList.length === 0) return;
    setChecking(true);
    setUploadMsg(null);
    try {
      const res = await api.products.compareAsins(asinList);
      setCompareResult(res);
      setUploadMsg({ ok: true, text: `重新比对：共 ${res.total}，匹配 ${res.matched}，缺失 ${res.missing}` });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "比对失败" });
    } finally {
      setChecking(false);
    }
  };

  const openExportFields = () => {
    setExportFields(COLUMNS.filter(c => visibility[c.key]).map(c => c.key));
    setShowExportFields(true);
  };

  const exportAsins = async (fields: string[]) => {
    setExporting(true);
    setUploadMsg(null);
    try {
      await api.products.exportAsins({
        keyword: debounced || undefined,
        filters: serializeFilters(debouncedFilters),
        status: statusFilter === "deleted" ? false : undefined,
        fields,
      });
      setUploadMsg({ ok: true, text: `ASIN 列表已导出（CSV，${fields.length} 个字段，含当前筛选条件）` });
      setShowExportFields(false);
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "导出失败" });
    } finally {
      setExporting(false);
    }
  };

  const exportExclude = async () => {
    setExporting(true);
    setUploadMsg(null);
    try {
      await api.products.exportExcludeAsins();
      setUploadMsg({ ok: true, text: "排除列表已导出（TXT，每行一个 ASIN）" });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "导出失败" });
    } finally {
      setExporting(false);
    }
  };

  const excludeClean = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setExcludeUploading(true);
    setUploadMsg(null);
    try {
      const res = await api.products.excludeAsins(file, true);
      setUploadMsg({
        ok: true,
        text: `清洗完成：共 ${res.total} 个ASIN，排除 ${res.excluded_count} 个（保留 ${res.kept_count} 个），库中已标记停用 ${res.marked_stopped} 个` +
          (res.sync_triggered ? "，已自动触发产品重新获取（排除列表生效）" : ""),
      });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "清洗失败" });
    } finally {
      setExcludeUploading(false);
    }
  };

  const keepClean = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setKeepUploading(true);
    setUploadMsg(null);
    try {
      const res = await api.products.keepAsins(file, true);
      setUploadMsg({
        ok: true,
        text: `恢复完成：共 ${res.total} 个ASIN，从排除列表移除 ${res.removed_from_exclude} 个，库中恢复在售 ${res.restored} 个` +
          (res.sync_triggered ? "，已自动触发产品重新获取（保留列表生效）" : ""),
      });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "恢复失败" });
    } finally {
      setKeepUploading(false);
    }
  };

  const resyncProducts = async () => {
    setSyncing(true);
    setUploadMsg(null);
    try {
      const res = await api.syncLogs.run("product");
      setUploadMsg({ ok: true, text: `${res.message}：已提交重新获取，同步完成后排除列表自动生效` });
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "同步提交失败" });
    } finally {
      setSyncing(false);
    }
  };

  // ── ASIN 排除/保留列表弹窗：打开时从参数读取，支持实时增删改查 ──
  const openListEditor = async (key: "listing_exclude_asins" | "listing_keep_asins") => {
    setConfigLoading(true);
    setUploadMsg(null);
    try {
      const params = await api.config.list();
      const item = params.find(p => p.key === key);
      const raw = String(item?.value ?? "");
      const asins = raw.split(/[\s,;，；]+/).filter(Boolean);
      setListEditor({ key, label: key === "listing_exclude_asins" ? "ASIN排除列表" : "ASIN保留列表", asins });
      setListInput("");
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "读取参数失败" });
    } finally {
      setConfigLoading(false);
    }
  };

  const addListAsins = () => {
    if (!listEditor || !listInput.trim()) return;
    const add = listInput.split(/[\s,;，；]+/).filter(Boolean);
    setListEditor({ ...listEditor, asins: Array.from(new Set([...listEditor.asins, ...add])) });
    setListInput("");
  };

  const removeListAsin = (asin: string) => {
    if (!listEditor) return;
    setListEditor({ ...listEditor, asins: listEditor.asins.filter(a => a !== asin) });
  };

  const saveListEditor = async () => {
    if (!listEditor) return;
    setListSaving(true);
    setUploadMsg(null);
    try {
      const value = listEditor.asins.join(",");
      await api.config.update(listEditor.key, value);
      setUploadMsg({ ok: true, text: `${listEditor.label} 已保存并生效（${listEditor.asins.length} 个ASIN），产品状态已同步` });
      setListEditor(null);
      setListInput("");
    } catch (err) {
      setUploadMsg({ ok: false, text: err instanceof Error ? err.message : "保存失败" });
    } finally {
      setListSaving(false);
    }
  };

  const products = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const visibleColumns = COLUMNS.filter(c => visibility[c.key]);

  const toggleColumn = (key: keyof Product) => {
    const next = { ...visibility, [key]: !visibility[key] };
    setVisibility(next);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  };

  const toggleFilterColumn = (key: keyof Product) => {
    const next = { ...filterVisibility, [key]: !filterVisibility[key] };
    setFilterVisibility(next);
    localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(next));
  };

  const setFilterValue = (key: keyof Product, val: FilterVal) => {
    setFilters(prev => {
      const empty =
        (val.op === "like" || val.op === "eq")
          ? String(val.value ?? "").trim() === ""
          : String(val.min ?? "") === "" && String(val.max ?? "") === "";
      const next = { ...prev };
      if (empty) delete next[key];
      else next[key] = val;
      return next;
    });
    setPage(0);
  };

  const filterInputStyle: React.CSSProperties = {
    backgroundColor: "var(--bg-secondary)",
    borderColor: "var(--border-color)",
    color: "var(--text-primary)",
  };

  // 筛选控件（按列类型渲染）
  const renderFilterInput = (c: ColumnDef) => {
    const key = c.key;
    const v = filters[key];
    if (key === "status") {
      return (
        <select
          value={statusFilter}
          onChange={e => { setStatusFilter(e.target.value as "active" | "deleted"); setPage(0); }}
          className="w-24 px-1.5 py-1 rounded border text-xs"
          style={filterInputStyle}
        >
          <option value="active">在售</option>
          <option value="deleted">停用</option>
        </select>
      );
    }
    if (key === "primary_operator") {
      return (
        <select
          value={(v && v.op === "eq" && String(v.value)) || ""}
          onChange={e => setFilterValue(key, { op: "eq", value: e.target.value })}
          className="w-32 px-1.5 py-1 rounded border text-xs"
          style={filterInputStyle}
        >
          <option value="">全部负责人</option>
          {operatorNames.map(n => <option key={n} value={n}>{n}</option>)}
        </select>
      );
    }
    const t = filterTypeOf(key);
    if (t === "select") {
      return (
        <select
          value={(v && v.op === "eq" && String(v.value)) || ""}
          onChange={e => setFilterValue(key, { op: "eq", value: e.target.value })}
          className="w-24 px-1.5 py-1 rounded border text-xs"
          style={filterInputStyle}
        >
          <option value="">全部</option>
          {(FILTER_OPTIONS[key] || []).map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      );
    }
    if (t === "range") {
      return (
        <div className="flex items-center gap-1 w-36">
          <input
            type="number"
            value={(v && v.op === "range" && v.min) || ""}
            placeholder="最小"
            onChange={e => setFilterValue(key, { op: "range", min: e.target.value, max: (v && v.op === "range" && v.max) || "" })}
            className="w-full px-1.5 py-1 rounded border text-xs"
            style={filterInputStyle}
          />
          <span style={{ color: "var(--text-tertiary)" }}>~</span>
          <input
            type="number"
            value={(v && v.op === "range" && v.max) || ""}
            placeholder="最大"
            onChange={e => setFilterValue(key, { op: "range", min: (v && v.op === "range" && v.min) || "", max: e.target.value })}
            className="w-full px-1.5 py-1 rounded border text-xs"
            style={filterInputStyle}
          />
        </div>
      );
    }
    if (t === "date") {
      return (
        <DateRangeFilter
          value={{ min: (v && v.op === "range" && v.min) || undefined, max: (v && v.op === "range" && v.max) || undefined }}
          onChange={next => setFilterValue(key, { op: "range", ...next })}
          style={filterInputStyle}
        />
      );
    }
    return (
      <input
        type="text"
        value={(v && (v.op === "like" || v.op === "eq")) ? String(v.value ?? "") : ""}
        placeholder="筛选..."
        onChange={e => setFilterValue(key, { op: "like", value: e.target.value })}
        className="w-40 px-1.5 py-1 rounded border text-xs"
        style={filterInputStyle}
      />
    );
  };

  const selectStyle: React.CSSProperties = {
    backgroundColor: "var(--bg-secondary)",
    borderColor: "var(--border-color)",
    color: "var(--text-primary)",
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <h1 className="text-2xl font-bold">产品列表</h1>
        <div className="flex gap-3 items-center flex-wrap">
          <input
            type="text"
            value={keyword}
            onChange={e => { setKeyword(e.target.value); setPage(0); }}
            placeholder="搜索 ASIN / 品名 / 分类..."
            className="text-sm px-3 py-1.5 rounded-md border w-56"
            style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
          />
          <button
            onClick={() => setShowColumns(!showColumns)}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}
          >
            字段显隐
          </button>
          <button
            onClick={() => setShowFilterPanel(!showFilterPanel)}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{
              backgroundColor: showFilterPanel ? "var(--accent-blue)" : "var(--bg-tertiary)",
              color: showFilterPanel ? "#fff" : "var(--text-primary)",
            }}
          >
            筛选显隐
            {Object.values(filterVisibility).some(Boolean) && (
              <span className="ml-1.5 px-1.5 py-0.5 rounded text-xs font-bold"
                style={{ backgroundColor: showFilterPanel ? "rgba(255,255,255,0.25)" : "var(--accent-blue)", color: "#fff" }}>
                {Object.values(filterVisibility).filter(Boolean).length}
              </span>
            )}
          </button>
        </div>
      </div>

      <div className="card mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-sm font-semibold">上传 ASIN 比对</h2>
          <label className="text-sm px-3 py-1.5 rounded-md border cursor-pointer" style={selectStyle}>
            {uploading ? "解析中..." : "选择文件"}
            <input type="file" accept=".txt,.csv,.xlsx,.xls" className="hidden" onChange={uploadFile} disabled={uploading} />
          </label>
          <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            支持 txt/csv/xlsx，自动读取 ASIN 与库中比对（不看状态）；缺失的自动触发产品同步
          </span>
          {asinList.length > 0 && (
            <button onClick={reCompare} disabled={checking}
              className="px-3 py-1.5 rounded-md text-sm font-medium"
              style={{ backgroundColor: checking ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
              {checking ? "比对中..." : "重新比对"}
            </button>
          )}
          {compareResult && compareResult.missing > 0 && (
            <button onClick={() => setShowMissing(s => !s)}
              className="px-3 py-1.5 rounded-md text-sm font-medium"
              style={{ backgroundColor: "rgba(239,68,68,0.12)", color: "var(--accent-red)" }}>
              {showMissing ? "收起缺失列表" : `查看缺失列表（${compareResult.missing}）`}
            </button>
          )}
        </div>
        {uploadMsg && (
          <p className="text-xs mt-2" style={{ color: uploadMsg.ok ? "var(--accent-green)" : "var(--accent-red)" }}>{uploadMsg.text}</p>
        )}
        {showMissing && compareResult && compareResult.missing_asins.length > 0 && (
          <div className="mt-3">
            <p className="text-xs font-medium mb-2" style={{ color: "var(--accent-red)" }}>
              缺失 ASIN（{compareResult.missing_asins.length}）— 可在同步完成后点「重新比对」
            </p>
            <div className="max-h-40 overflow-y-auto rounded-md p-2" style={{ backgroundColor: "var(--bg-tertiary)" }}>
              <p className="font-mono text-xs break-all" style={{ color: "var(--text-secondary)" }}>
                {compareResult.missing_asins.join("、")}
              </p>
            </div>
          </div>
        )}
      </div>

      <div className="card mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-sm font-semibold">ASIN 导出与清洗</h2>
          <button onClick={openExportFields} disabled={exporting}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: exporting ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
            {exporting ? "导出中..." : "导出 ASIN(CSV) 选字段"}
          </button>
          <button onClick={exportExclude} disabled={exporting}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: exporting ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
            导出排除列表(TXT)
          </button>
          <button onClick={() => openListEditor("listing_exclude_asins")} disabled={configLoading}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: configLoading ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
            ASIN排除列表
          </button>
          <button onClick={() => openListEditor("listing_keep_asins")} disabled={configLoading}
            className="px-3 py-1.5 rounded-md text-sm font-medium"
            style={{ backgroundColor: configLoading ? "#94a3b8" : "var(--bg-tertiary)", color: "var(--text-primary)" }}>
            ASIN保留列表
          </button>
          <label className="text-sm px-3 py-1.5 rounded-md border cursor-pointer"
            style={{ backgroundColor: excludeUploading ? "#94a3b8" : "rgba(239,68,68,0.12)", color: excludeUploading ? "#fff" : "var(--accent-red)", borderColor: "var(--border-color)" }}>
            {excludeUploading ? "清洗中..." : "上传不需要的ASIN并清洗"}
            <input type="file" accept=".txt,.csv,.xlsx,.xls" className="hidden" onChange={excludeClean} disabled={excludeUploading} />
          </label>
          <label className="text-sm px-3 py-1.5 rounded-md border cursor-pointer"
            style={{ backgroundColor: keepUploading ? "#94a3b8" : "rgba(16,185,129,0.12)", color: keepUploading ? "#fff" : "var(--accent-green)", borderColor: "var(--border-color)" }}>
            {keepUploading ? "恢复中..." : "上传需要的ASIN并恢复"}
            <input type="file" accept=".txt,.csv,.xlsx,.xls" className="hidden" onChange={keepClean} disabled={keepUploading} />
          </label>
          <button onClick={resyncProducts} disabled={syncing}
            className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: syncing ? "#94a3b8" : "var(--accent-blue)" }}>
            {syncing ? "提交中..." : "重新获取产品数据"}
          </button>
          <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
            导出当前筛选 ASIN → 标记不需要的 → 上传清洗（合并进排除列表并停用）→ 自动重新获取；误排除可用「上传需要的ASIN并恢复」加回
          </span>
        </div>
      </div>

      {COLUMNS.some(c => filterVisibility[c.key]) && (
        <div className="card mb-4">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            {COLUMNS.filter(c => filterVisibility[c.key]).map(c => (
              <div key={c.key} className="flex items-center gap-2 text-sm">
                <span className="whitespace-nowrap text-xs font-medium" style={{ color: "var(--text-tertiary)" }}>{c.label}</span>
                {renderFilterInput(c)}
              </div>
            ))}
          </div>
        </div>
      )}

      {showColumns && (
        <div className="card mb-4">
          <h3 className="text-sm font-semibold mb-3">可见字段（共 {COLUMNS.length} 个，勾选控制显示）</h3>
          <div className="flex flex-wrap gap-4">
            {COLUMNS.map(c => (
              <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={!!visibility[c.key]} onChange={() => toggleColumn(c.key)} />
                {c.label}
              </label>
            ))}
          </div>
        </div>
      )}

      {showFilterPanel && (
        <div className="card mb-4">
          <h3 className="text-sm font-semibold mb-3">列筛选显隐（共 {COLUMNS.length} 个，勾选后该列表头显示筛选按钮；全部列皆可筛选）</h3>
          <div className="flex flex-wrap items-center gap-2 mb-3">
            <button
              onClick={() => {
                const next = Object.fromEntries(COLUMNS.map(c => [c.key, true]));
                setFilterVisibility(next);
                localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(next));
              }}
              className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>全选</button>
            <button
              onClick={() => {
                const next = Object.fromEntries(COLUMNS.map(c => [c.key, false]));
                setFilterVisibility(next);
                localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(next));
              }}
              className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>清空</button>
            <button
              onClick={() => {
                const next = Object.fromEntries(FILTER_DEFAULT_ON.map(k => [k, true]));
                setFilterVisibility(next);
                localStorage.setItem(FILTER_STORAGE_KEY, JSON.stringify(next));
              }}
              className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>恢复默认</button>
          </div>
          <div className="flex flex-wrap gap-4">
            {COLUMNS.map(c => (
              <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                <input type="checkbox" checked={!!filterVisibility[c.key]}
                  onChange={() => toggleFilterColumn(c.key)} />
                {c.label}
                <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                  {filterTypeOf(c.key) === "select" ? "下拉" : filterTypeOf(c.key) === "range" ? "数值区间" : filterTypeOf(c.key) === "date" ? "日期区间" : "文本"}
                </span>
              </label>
            ))}
          </div>
        </div>
      )}

      {listEditor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(15,23,42,0.5)" }}>
          <div className="card w-full max-w-2xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold mb-1">{listEditor.label}</h3>
            <p className="text-sm mb-3" style={{ color: "var(--text-tertiary)" }}>
              支持逐行/逗号分隔；保存后会与产品库同步（排除列表停用、保留列表恢复在售），保留列表优先级高于排除列表
            </p>
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <input
                value={listInput}
                onChange={e => setListInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") addListAsins(); }}
                placeholder="输入一个或多个 ASIN（逗号/换行分隔），回车添加"
                className="flex-1 min-w-[240px] px-3 py-1.5 rounded-md text-sm border"
                style={{ backgroundColor: "var(--bg-primary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />
              <button onClick={addListAsins}
                className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: "var(--accent-blue)" }}>
                添加
              </button>
              <span className="text-xs self-center" style={{ color: "var(--text-tertiary)" }}>共 {listEditor.asins.length} 个ASIN</span>
            </div>
            <div className="flex flex-wrap gap-1.5 min-h-[120px] max-h-[300px] overflow-y-auto p-2 rounded-md"
              style={{ backgroundColor: "var(--bg-tertiary)" }}>
              {listEditor.asins.length === 0 && (
                <span className="text-xs self-center" style={{ color: "var(--text-tertiary)" }}>列表为空</span>
              )}
              {listEditor.asins.map(a => (
                <span key={a} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-medium"
                  style={{ backgroundColor: "var(--bg-primary)", color: "var(--text-primary)" }}>
                  <span className="font-mono">{a}</span>
                  <button onClick={() => removeListAsin(a)} aria-label={`删除 ${a}`}
                    style={{ color: "var(--accent-red)" }}>×</button>
                </span>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => { setListEditor(null); setListInput(""); }}
                className="px-3 py-1.5 rounded-md text-sm font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>取消</button>
              <button onClick={saveListEditor} disabled={listSaving}
                className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: listSaving ? "#94a3b8" : "var(--accent-blue)" }}>
                {listSaving ? "保存中..." : "保存并生效"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showExportFields && (
        <div className="fixed inset-0 z-50 flex items-center justify-center" style={{ backgroundColor: "rgba(15,23,42,0.5)" }}>
          <div className="card w-full max-w-3xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-semibold mb-1">选择导出字段（共 {COLUMNS.length} 个，与字段显隐范围一致）</h3>
            <p className="text-sm mb-3" style={{ color: "var(--text-tertiary)" }}>默认按当前字段显隐；可全选/清空/按当前显隐调整</p>
            <div className="flex flex-wrap items-center gap-2 mb-3">
              <button onClick={() => setExportFields(COLUMNS.map(c => c.key))}
                className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>全选</button>
              <button onClick={() => setExportFields([])}
                className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>清空</button>
              <button onClick={() => setExportFields(COLUMNS.filter(c => visibility[c.key]).map(c => c.key))}
                className="px-2 py-1 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>按当前显隐</button>
              <span className="text-xs self-center" style={{ color: "var(--text-tertiary)" }}>已选 {exportFields.length} 个字段</span>
            </div>
            <div className="flex flex-wrap gap-4">
              {COLUMNS.map(c => (
                <label key={c.key} className="flex items-center gap-2 text-sm cursor-pointer">
                  <input type="checkbox" checked={exportFields.includes(c.key)}
                    onChange={() => setExportFields(prev =>
                      prev.includes(c.key) ? prev.filter(k => k !== c.key) : [...prev, c.key])} />
                  {c.label}
                </label>
              ))}
            </div>
            <div className="flex justify-end gap-2 mt-4">
              <button onClick={() => setShowExportFields(false)}
                className="px-3 py-1.5 rounded-md text-sm font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>取消</button>
              <button onClick={() => exportAsins(exportFields)} disabled={exporting || exportFields.length === 0}
                className="px-3 py-1.5 rounded-md text-sm font-medium text-white"
                style={{ backgroundColor: exporting || exportFields.length === 0 ? "#94a3b8" : "var(--accent-blue)" }}>
                {exporting ? "导出中..." : `导出（${exportFields.length} 个字段）`}
              </button>
            </div>
          </div>
        </div>
      )}

      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        {debounced ? `搜索 "${debounced}"：` : ""}共 {total} 个产品（{statusFilter === "active" ? "已排除已删除/停用商品" : "仅显示已删除/停用商品"}）
      </p>

      {loading && <p>加载中...</p>}
      {!loading && products.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>未找到产品</div>}
      {!loading && products.length > 0 && (
        <div className="card overflow-auto" style={{ maxHeight: "calc(100vh - 240px)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                {visibleColumns.map(c => (
                  <th key={c.key} className={`${c.align === "right" ? "text-right" : c.align === "center" ? "text-center" : "text-left"} py-2 pr-3 whitespace-nowrap`}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {products.map(p => (
                <tr
                  key={p.asin}
                  style={{ borderBottom: "1px solid var(--border-color)" }}
                  onClick={() => router.push(`/products/${p.asin}`)}
                  className="cursor-pointer"
                  onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                  onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}
                >
                  {visibleColumns.map(c => {
                    if (c.key === "product_level") {
                      const lv = p.product_level || "";
                      const ls = LEVEL_STYLE[lv] || LEVEL_STYLE.D;
                      return (
                        <td key={c.key} className="py-2 pr-3">
                          {lv && <span className="px-1.5 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: ls.bg, color: ls.color }}>{lv}</span>}
                        </td>
                      );
                    }
                    if (c.key === "status") {
                      return (
                        <td key={c.key} className="py-2 pr-3">
                          <span className={`inline-block w-2 h-2 rounded-full ${p.status ? "bg-green-500" : "bg-red-400"}`} />
                        </td>
                      );
                    }
                    if (c.key === "asin") {
                      return <td key={c.key} className="py-2 pr-3 font-mono text-xs whitespace-nowrap">{p.asin}</td>;
                    }
                    if (c.key === "product_name") {
                      return <td key={c.key} className="py-2 pr-3 truncate max-w-xs">{p.product_name}</td>;
                    }
                    return (
                      <td key={c.key} className={`py-2 pr-3 truncate max-w-xs ${c.align === "right" ? "text-right" : ""}`}>{cellValue(p, c.key)}</td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {total > pageSize && (
        <div className="flex items-center justify-between mt-4 text-sm flex-wrap gap-3">
          <div className="flex items-center gap-2" style={{ color: "var(--text-tertiary)" }}>
            <span>每页</span>
            <select
              value={pageSize}
              onChange={e => { setPageSize(Number(e.target.value)); setPage(0); }}
              className="px-2 py-1 rounded border text-xs"
              style={selectStyle}
            >
              {PAGE_SIZES.map(n => <option key={n} value={n}>{n} 条</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <span style={{ color: "var(--text-tertiary)" }}>第 {page + 1} / {totalPages} 页</span>
            <button
              disabled={page === 0}
              onClick={() => setPage(p => Math.max(0, p - 1))}
              className="px-3 py-1.5 rounded-md"
              style={{ backgroundColor: page === 0 ? "var(--bg-tertiary)" : "var(--accent-blue)", color: page === 0 ? "var(--text-tertiary)" : "#fff" }}
            >
              上一页
            </button>
            <button
              disabled={page + 1 >= totalPages}
              onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
              className="px-3 py-1.5 rounded-md"
              style={{ backgroundColor: page + 1 >= totalPages ? "var(--bg-tertiary)" : "var(--accent-blue)", color: page + 1 >= totalPages ? "var(--text-tertiary)" : "#fff" }}
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
