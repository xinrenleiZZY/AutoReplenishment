"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api, pollJob, type CostTable, type Operator, type ProductCostOverride } from "@/lib/api";

export default function ProductDetailPage() {
  const params = useParams();
  const asin = params.asin as string;
  const [product, setProduct] = useState<any>(null);
  const [latestCalc, setLatestCalc] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [operators, setOperators] = useState<Operator[]>([]);
  const [editingOperator, setEditingOperator] = useState(false);
  const [operatorText, setOperatorText] = useState("");
  const [savingOperator, setSavingOperator] = useState(false);
  const [operatorMsg, setOperatorMsg] = useState<string | null>(null);
  const [acosText, setAcosText] = useState("");
  const [savingAcos, setSavingAcos] = useState(false);
  const [acosMsg, setAcosMsg] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);
  const [calcMsg, setCalcMsg] = useState<string | null>(null);
  const [costTable, setCostTable] = useState<CostTable | null>(null);
  const [costForm, setCostForm] = useState<Record<string, string>>({});
  const [initialCostForm, setInitialCostForm] = useState<Record<string, string>>({});
  const [costOverriddenKeys, setCostOverriddenKeys] = useState<Set<string>>(new Set());
  const [costSaving, setCostSaving] = useState(false);
  const [costMsg, setCostMsg] = useState<string | null>(null);
  const [costImporting, setCostImporting] = useState(false);

  useEffect(() => {
    Promise.all([
      api.products.get(asin).catch(() => null),
      api.calculation.latest(asin).catch(() => null),
    ]).then(([prod, calc]) => { setProduct(prod); setLatestCalc(calc); }).finally(() => setLoading(false));
  }, [asin, refresh]);

  const analyzeNow = async () => {
    setAnalyzing(true);
    setCalcMsg(null);
    try {
      const res = await api.calculation.trigger(asin);
      const job = await pollJob(res.job_id);
      if (job.status === "failed") throw new Error(job.error || "分析失败");
      setCalcMsg("分析完成，结果已更新");
      setRefresh(r => r + 1);
    } catch (e) {
      setCalcMsg(e instanceof Error ? e.message : "分析失败");
    } finally {
      setAnalyzing(false);
    }
  };

  useEffect(() => {
    api.operators.list({ status: true }).then(setOperators).catch(() => setOperators([]));
  }, []);

  const loadCost = useCallback(async () => {
    try {
      const res = await api.products.cost.get(asin);
      setCostTable(res.table);
      const ov = res.overrides ?? {};
      const t = res.table;
      const sea = t.channels.sea;
      const form: Record<string, string> = {
        price: t.price != null ? String(t.price) : "",
        cost_cny: t.cost_price_cny != null ? String(t.cost_price_cny) : "",
        exchange_rate: t.exchange_rate != null ? String(t.exchange_rate) : "",
        length_cm: t.dims_cm?.[0] ? String(t.dims_cm[0]) : "",
        width_cm: t.dims_cm?.[1] ? String(t.dims_cm[1]) : "",
        height_cm: t.dims_cm?.[2] ? String(t.dims_cm[2]) : "",
        weight_kg: t.weight_kg ? String(t.weight_kg) : "",
        freight_sea_cny: sea?.freight_fee != null ? String(sea.freight_fee) : "",
        freight_air_cny: t.channels.air?.freight_fee != null ? String(t.channels.air.freight_fee) : "",
        freight_express_cny: t.channels.express?.freight_fee != null ? String(t.channels.express.freight_fee) : "",
        sorting_fee: sea?.sorting_fee != null ? String(sea.sorting_fee) : "",
        referral_fee: sea?.referral_fee != null ? String(sea.referral_fee) : "",
        packing_fee: sea?.packing_card != null ? String(sea.packing_card) : "",
        inbound_fee: sea?.inbound_fee != null ? String(sea.inbound_fee) : "",
        storage_fee: sea?.storage_fee != null ? String(sea.storage_fee) : "",
        ad_fee: sea?.ad_fee != null ? String(sea.ad_fee) : "",
        return_loss: sea?.return_loss != null ? String(sea.return_loss) : "",
        over_threshold_loss: sea?.over_threshold_loss != null ? String(sea.over_threshold_loss) : "",
        misc_fee: sea?.misc_fee != null ? String(sea.misc_fee) : "",
        notes: (ov.notes as string) ?? "",
      };
      setCostForm(form);
      setInitialCostForm(form);
      setCostOverriddenKeys(new Set(Object.keys(ov)));
    } catch {
      setCostTable(null);
    }
  }, [asin]);

  useEffect(() => { loadCost(); }, [loadCost]);

  const saveCost = async () => {
    setCostSaving(true);
    setCostMsg(null);
    const data: Record<string, number | string | null> = {};
    (Object.keys(costForm)).forEach(k => {
      const v = costForm[k].trim();
      if (v !== (initialCostForm[k] ?? "")) {
        data[k] = v === "" ? null : k === "notes" ? v : Number(v);
      }
    });
    if (Object.keys(data).length === 0) {
      setCostMsg("没有修改");
      setCostSaving(false);
      return;
    }
    try {
      await api.products.cost.update(asin, data as unknown as ProductCostOverride);
      setCostMsg("成本表已保存（输入框为当前生效值，改动项才会覆盖）");
      await loadCost();
    } catch (e) {
      setCostMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setCostSaving(false);
    }
  };

  const importCostFile = async (file: File | undefined) => {
    if (!file) return;
    setCostImporting(true);
    setCostMsg(null);
    try {
      const res = await api.products.cost.import(file);
      setCostMsg(res.message);
      await loadCost();
    } catch (e) {
      setCostMsg(e instanceof Error ? e.message : "导入失败");
    } finally {
      setCostImporting(false);
    }
  };

  const costFields: { key: string; label: string }[] = [
    { key: "price", label: "售价 $" },
    { key: "cost_cny", label: "采购总成本 ￥" },
    { key: "exchange_rate", label: "汇率" },
    { key: "length_cm", label: "包装长 cm" },
    { key: "width_cm", label: "包装宽 cm" },
    { key: "height_cm", label: "包装高 cm" },
    { key: "weight_kg", label: "毛重 kg" },
    { key: "freight_sea_cny", label: "海运运费 ￥" },
    { key: "freight_air_cny", label: "空派运费 ￥" },
    { key: "freight_express_cny", label: "快递运费 ￥" },
    { key: "sorting_fee", label: "分拣费 $" },
    { key: "referral_fee", label: "佣金 $" },
    { key: "packing_fee", label: "P卡费 $" },
    { key: "inbound_fee", label: "入库配置费 $" },
    { key: "storage_fee", label: "仓储费 $" },
    { key: "ad_fee", label: "广告费 $" },
    { key: "return_loss", label: "退货平摊 $" },
    { key: "over_threshold_loss", label: "超阈值亏损 $" },
    { key: "misc_fee", label: "附加费 $" },
    { key: "notes", label: "备注" },
  ];

  const startEditOperator = () => {
    setOperatorText(product?.operator ?? "");
    setEditingOperator(true);
    setOperatorMsg(null);
  };

  const toggleName = (name: string) => {
    const list = operatorText.split(",").map(s => s.trim()).filter(Boolean);
    if (list.includes(name)) {
      setOperatorText(list.filter(n => n !== name).join(", "));
    } else {
      setOperatorText([...list, name].join(", "));
    }
  };

  const saveOperator = async () => {
    setSavingOperator(true);
    setOperatorMsg(null);
    try {
      const updated = await api.products.update(asin, { operator: operatorText.trim() });
      setProduct(updated);
      setEditingOperator(false);
      setOperatorMsg("负责人已更新");
    } catch (e) {
      setOperatorMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingOperator(false);
    }
  };

  const saveAcos = async () => {
    setSavingAcos(true);
    setAcosMsg(null);
    try {
      const val = acosText.trim() === "" ? null : Number(acosText.trim());
      if (val !== null && !Number.isFinite(val)) throw new Error("ACOS 必须是数字（小数，如 0.45）");
      const updated = await api.products.update(asin, { acos_30d: val });
      setProduct(updated);
      setAcosMsg("ACOS 已保存");
    } catch (e) {
      setAcosMsg(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingAcos(false);
    }
  };

  if (loading) return <p>加载中...</p>;
  if (!product) return <div className="card text-center py-12">产品不存在</div>;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex py-2" style={{ borderBottom: "1px solid var(--border-color)" }}>
      <span className="w-36 text-sm shrink-0" style={{ color: "var(--text-tertiary)" }}>{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  );

  const Section = ({ title, children }: { title: string; children: React.ReactNode }) => (
    <div className="card">
      <h2 className="text-lg font-semibold mb-4">{title}</h2>
      {children}
    </div>
  );

  const rows = (fields: [string, React.ReactNode][]) => fields.map(([label, value]) => (
    <Row key={label} label={label} value={value} />
  ));

  const fmtNum = (v: any) => (v === null || v === undefined || v === "" ? "-" : Number(v).toLocaleString());
  const fmtStr = (v: any) => (v === null || v === undefined || v === "" ? "-" : String(v));
  const fmtUsd = (v: any) => (v === null || v === undefined || v === "" ? "-" : `$${v}`);
  const fmtCny = (v: any) => (v === null || v === undefined || v === "" ? "-" : `￥${v}`);
  const fmtJson = (v: any, max = 160) => {
    if (!v) return "-";
    let s = String(v);
    try {
      const arr = JSON.parse(s);
      if (Array.isArray(arr)) s = arr.map((x: any) => (typeof x === "string" ? x : x?.tagName || x?.realname || JSON.stringify(x))).join("、");
    } catch { /* ignore */ }
    return s.length > max ? s.slice(0, max) + "…" : s;
  };

  return (
    <div>
      <Link href="/products" className="text-sm mb-4 inline-block" style={{ color: "var(--accent-blue)" }}>← 返回产品列表</Link>
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <h1 className="text-2xl font-bold">{product.product_name}</h1>
        <div className="flex items-center gap-3">
          {calcMsg && <span className="text-sm" style={{ color: calcMsg.includes("失败") ? "var(--accent-red)" : "var(--accent-green)" }}>{calcMsg}</span>}
          <button
            onClick={analyzeNow}
            disabled={analyzing}
            className="px-4 py-1.5 rounded-md text-sm font-medium text-white"
            style={{ backgroundColor: analyzing ? "#94a3b8" : "var(--accent-green)" }}
          >
            {analyzing ? "分析中..." : "立即分析"}
          </button>
        </div>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Section title="基础信息">
          {rows([
            ["ASIN", <span key="asin" className="font-mono">{product.asin}</span>],
            ["MSKU", <span key="msku" className="font-mono">{fmtStr(product.msku)}</span>],
            ["本地SKU", <span key="lsku" className="font-mono">{fmtStr(product.local_sku)}</span>],
            ["FNSKU", <span key="fnsku" className="font-mono">{fmtStr(product.fnsku)}</span>],
            ["品名", fmtStr(product.local_name || product.product_name)],
            ["分类", fmtStr(product.category)],
            ["子分类", fmtStr(product.sub_category)],
            ["品牌", fmtStr(product.brand || product.brand_name)],
            ["所属节日", fmtStr(product.festival)],
            ["产品类型", fmtStr(product.product_type)],
            ["负责人", (
              <span key="op" className="inline-flex items-center gap-2">
                <span className="flex flex-wrap gap-1">
                  {product.primary_operator ? (
                    <span className="px-1.5 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>{product.primary_operator}</span>
                  ) : "-"}
                </span>
                <button onClick={startEditOperator} className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--accent-blue)" }}>编辑</button>
              </span>
            )],
            ["上架日期", fmtStr(product.list_date)],
            ["利润率", product.profit_rate != null ? `${(product.profit_rate * 100).toFixed(1)}%` : "-"],
            ["30天ACOS", (
              <span key="acos" className="inline-flex items-center gap-2">
                <input
                  type="number"
                  step="0.01"
                  min="0"
                  max="1"
                  value={acosText}
                  placeholder={product.acos_30d != null ? String(product.acos_30d) : "未填写"}
                  onChange={e => { setAcosText(e.target.value); setAcosMsg(null); }}
                  className="px-2 py-1 rounded border w-24 text-right"
                  style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
                />
                <button
                  onClick={saveAcos}
                  disabled={savingAcos}
                  className="px-2 py-1 rounded text-xs font-medium text-white"
                  style={{ backgroundColor: savingAcos ? "#94a3b8" : "var(--accent-blue)" }}
                >
                  保存
                </button>
                {acosMsg && <span className="text-xs" style={{ color: "var(--accent-green)" }}>{acosMsg}</span>}
              </span>
            )],
            ["状态", <span key="st" className={`inline-block w-2 h-2 rounded-full ${product.status ? "bg-green-500" : "bg-red-400"}`} />],
          ])}
          {editingOperator && (
            <div className="py-3" style={{ borderBottom: "1px solid var(--border-color)" }}>
              <p className="text-xs mb-2" style={{ color: "var(--text-tertiary)" }}>负责人支持单人/多人：勾选或输入姓名，多个用逗号分隔。</p>
              <div className="flex flex-wrap gap-2 mb-2">
                {operators.map(op => {
                  const checked = operatorText.split(",").map(s => s.trim()).includes(op.name);
                  return (
                    <label key={op.id} className="flex items-center gap-1.5 text-xs cursor-pointer px-2 py-1 rounded" style={{ backgroundColor: checked ? "rgba(34,197,94,0.12)" : "var(--bg-tertiary)" }}>
                      <input type="checkbox" checked={checked} onChange={() => toggleName(op.name)} />
                      {op.name}
                    </label>
                  );
                })}
                {operators.length === 0 && <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>暂无运营人员，可先到「运营人员管理」新增</span>}
              </div>
              <input type="text" value={operatorText} onChange={e => setOperatorText(e.target.value)} placeholder="输入负责人姓名，多个用逗号分隔"
                className="text-sm px-3 py-1.5 rounded-md border w-full mb-2"
                style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }} />
              <div className="flex items-center gap-2">
                <button onClick={saveOperator} disabled={savingOperator} className="px-3 py-1 rounded-md text-xs font-medium text-white"
                  style={{ backgroundColor: savingOperator ? "#94a3b8" : "var(--accent-green)" }}>
                  {savingOperator ? "保存中..." : "保存"}
                </button>
                <button onClick={() => setEditingOperator(false)} className="px-3 py-1 rounded-md text-xs font-medium"
                  style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>取消</button>
                {operatorMsg && <span className="text-xs" style={{ color: "var(--accent-green)" }}>{operatorMsg}</span>}
              </div>
            </div>
          )}
        </Section>
        <Section title="生命周期与等级">
          {rows([
            ["阶段", <span key="stg" className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: product.product_stage === "新品" ? "#dbeafe" : "#f1f5f9" }}>{fmtStr(product.product_stage)}</span>],
            ["生命周期", fmtStr(product.life_cycle)],
            ["等级", product.product_level ? <span key="lv" className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: product.product_level === "S" ? "#fef3c7" : product.product_level === "A" ? "#dbeafe" : product.product_level === "B" ? "#dcfce7" : "#f1f5f9", color: product.product_level === "S" ? "#92400e" : product.product_level === "A" ? "#1e40af" : product.product_level === "B" ? "#166534" : "#64748b" }}>{product.product_level}</span> : "-"],
            ["计算频率", product.calc_frequency ? <span key="cf" className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>{product.calc_frequency}</span> : "-"],
            ["工期(天)", fmtNum(product.lead_time)],
            ["单箱数量", fmtNum(product.box_quantity)],
            ["最低采购量", fmtNum(product.min_order_qty)],
            ["核心月份", fmtStr(product.core_months)],
          ])}
        </Section>
        <Section title="价格与费用">
          {rows([
            ["售价", product.price ? `${product.currency_symbol ?? "$"}${product.price}` : "-"],
            ["Listing价", fmtUsd(product.listing_price)],
            ["落地价", fmtUsd(product.landed_price)],
            ["日常价", fmtUsd(product.regular_price)],
            ["目录价", fmtUsd(product.list_price)],
            ["B2B价", fmtUsd(product.b2b_price)],
            ["FBA费用", fmtUsd(product.fba_fee)],
            ["佣金", fmtUsd(product.referral_fee)],
            ["运费", fmtUsd(product.shipping)],
            ["采购报价", fmtCny(product.cost_price)],
            ["历史价格", fmtUsd(product.history_price)],
          ])}
        </Section>
        <Section title="销量与广告">
          {rows([
            ["30天销量", fmtNum(product.thirty_volume)],
            ["7天销量", fmtNum(product.seven_volume)],
            ["昨日销量", fmtNum(product.yesterday_volume)],
            ["历史总销量", fmtNum(product.total_volume)],
            ["30天日均", fmtNum(product.average_thirty_volume)],
            ["7天日均", fmtNum(product.average_seven_volume)],
            ["30天销售额", fmtUsd(product.thirty_amount)],
            ["7天销售额", fmtUsd(product.seven_amount)],
            ["昨日销售额", fmtUsd(product.yesterday_amount)],
            ["30天广告", fmtUsd(product.thirty_spend)],
            ["7天广告", fmtUsd(product.seven_spend)],
            ["昨日广告", fmtUsd(product.yesterday_spend)],
          ])}
        </Section>
        <Section title="库存（FBA）">
          {rows([
            ["FBA可售", fmtNum(product.afn_fulfillable_quantity)],
            ["FBA预留", fmtNum(product.afn_reserved_quantity)],
            ["在途", fmtNum(product.afn_inbound_shipped_quantity)],
            ["待到货量", fmtNum(product.purchase_on_order)],
            ["不可售", fmtNum(product.afn_unsellable_quantity)],
            ["入库中", fmtNum(product.afn_inbound_working_quantity)],
            ["待发货", fmtNum(product.afn_inbound_receiving_quantity)],
            ["待调仓", fmtNum(product.reserved_fc_transfers)],
            ["客户订单预留", fmtNum(product.reserved_customerorders)],
            ["当前数量", fmtNum(product.quantity)],
          ])}
        </Section>
        <Section title="排名与表现">
          {rows([
            ["大类排名", fmtNum(product.rank)],
            ["卖家排名", fmtNum(product.seller_rank)],
            ["类目排名", fmtJson(product.category_rank)],
            ["小类排名", fmtJson(product.small_rank)],
            ["卖家类目", fmtStr(product.seller_category)],
            ["评分", fmtStr(product.stars)],
            ["评论数", fmtNum(product.reviews_num)],
          ])}
        </Section>
        <Section title="店铺与运营">
          {rows([
            ["店铺", fmtStr(product.shop)],
            ["站点", fmtStr(product.marketplace)],
            ["卖家名称", fmtStr(product.seller_name)],
            ["配送渠道", fmtStr(product.fulfillment_channel_type)],
            ["状态文本", fmtStr(product.status_text)],
            ["创建人", fmtStr(product.product_creator_realname)],
            ["开发人", fmtStr(product.product_developer)],
            ["负责人列表", fmtJson(product.principal_list)],
            ["权限用户", fmtJson(product.permission_user_info)],
          ])}
        </Section>
        <Section title="标签与采购档案">
          {rows([
            ["标签", fmtJson(product.tags)],
            ["供应商", fmtStr(product.supplier_name)],
            ["亚马逊产品类型", fmtStr(product.amz_product_type)],
            ["备注", fmtStr(product.remark)],
          ])}
        </Section>
      </div>
      <div className="card mt-6">
        <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
          <h2 className="text-lg font-semibold">成本表（三渠道 Profit）</h2>
          <div className="flex items-center gap-2">
            <label className="px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer"
              style={{ backgroundColor: "var(--bg-tertiary)", color: "var(--text-primary)" }}>
              {costImporting ? "导入中..." : "导入Excel"}
              <input type="file" accept=".xlsx,.xls" className="hidden"
                onChange={e => { importCostFile(e.target.files?.[0]); e.target.value = ""; }} />
            </label>
            <button onClick={saveCost} disabled={costSaving}
              className="px-3 py-1.5 rounded-md text-xs font-medium text-white"
              style={{ backgroundColor: costSaving ? "#94a3b8" : "var(--accent-blue)" }}>
              {costSaving ? "保存中..." : "保存"}
            </button>
          </div>
        </div>
        {costMsg && <p className="text-xs mb-2" style={{ color: costMsg.includes("失败") || costMsg.includes("错误") ? "var(--accent-red)" : "var(--accent-green)" }}>{costMsg}</p>}
        <p className="text-xs mb-3" style={{ color: "var(--text-tertiary)" }}>
          优先使用本页覆盖值；空字段自动用产品档案/系统配置。导入Excel需含 ASIN 列（ASIN 或 链接/ASIN），其余列名可参考：售价/采购总成本/包装长宽高/毛重/海运运费/空派运费/快递运费/分拣费/佣金/P卡/入库配置费/仓储费/广告费/退货成本/附加费。
        </p>
        <p className="text-xs mb-2" style={{ color: "var(--text-tertiary)" }}>
          输入框显示当前生效值；带 <span className="px-1 rounded text-[9px]" style={{ backgroundColor: "#dbeafe", color: "#1e40af" }}>自</span> 标记的为已自定义覆盖，保存只写改动项。
        </p>
        <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-10 gap-2 mb-4">
          {costFields.map(f => (
            <label key={f.key} className="text-[11px]" style={{ color: "var(--text-tertiary)" }}>
              {f.label}
              {costOverriddenKeys.has(f.key) && <span className="ml-1 px-1 rounded text-[9px]" style={{ backgroundColor: "#dbeafe", color: "#1e40af" }}>自</span>}
              <input
                type={f.key === "notes" ? "text" : "number"}
                step="any"
                value={costForm[f.key] ?? ""}
                placeholder="默认"
                onChange={e => setCostForm(prev => ({ ...prev, [f.key]: e.target.value }))}
                className="mt-0.5 w-full px-1.5 py-1 rounded border text-xs text-right"
                style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
              />
            </label>
          ))}
        </div>
        {costTable && (
          <>
            <table className="w-full text-sm mb-3">
              <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
                <th className="text-left py-2 pr-3">渠道</th>
                <th className="text-right py-2 pr-3">运费(￥)</th>
                <th className="text-right py-2 pr-3">运费($)</th>
                <th className="text-right py-2 pr-3">MC($)</th>
                <th className="text-right py-2 pr-3">单件利润($)</th>
                <th className="text-right py-2 pr-3">毛利率</th>
                <th className="text-center py-2">是否盈利</th>
              </tr></thead>
              <tbody>
                {(["sea", "air", "express"] as const).map(mode => {
                  const c = costTable.channels[mode];
                  if (!c) return null;
                  return (
                    <tr key={mode} style={{ borderBottom: "1px solid var(--border-color)" }}>
                      <td className="py-2 pr-3">{c.label}</td>
                      <td className="py-2 pr-3 text-right">￥{c.freight_fee}</td>
                      <td className="py-2 pr-3 text-right">${c.freight_fee_usd.toFixed(2)}</td>
                      <td className="py-2 pr-3 text-right">${c.mc?.toFixed(2) ?? "-"}</td>
                      <td className="py-2 pr-3 text-right font-mono" style={{ color: c.profit >= 0 ? "var(--accent-green)" : "var(--accent-red)" }}>
                        ${c.profit.toFixed(2)}
                      </td>
                      <td className="py-2 pr-3 text-right">{c.margin != null ? `${(c.margin * 100).toFixed(1)}%` : "-"}</td>
                      <td className="py-2 text-center">
                        <span className="px-2 py-0.5 rounded text-xs font-medium"
                          style={{ backgroundColor: c.profitable ? "#dcfce7" : "#fee2e2", color: c.profitable ? "#166534" : "#dc2626" }}>
                          {c.profitable ? "盈利" : "亏损"}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div className="text-xs space-y-1 mt-1" style={{ color: "var(--text-tertiary)" }}>
              <p>
                包装尺寸：{costTable.dims_cm && costTable.dims_cm.every(d => d > 0)
                  ? `${costTable.dims_cm[0]}×${costTable.dims_cm[1]}×${costTable.dims_cm[2]} cm`
                  : "未填"} ｜ 毛重：{costTable.weight_kg ? `${costTable.weight_kg} kg` : "未填"} ｜ 采购成本：￥{costTable.cost_price_cny ?? "-"} → ${costTable.unit_cost.toFixed(4)} ｜ 汇率：{costTable.exchange_rate ?? 6.5}
              </p>
              <p>
                运费（海运/空派/快递）：￥{costTable.channels.sea?.freight_fee ?? "-"} / ￥{costTable.channels.air?.freight_fee ?? "-"} / ￥{costTable.channels.express?.freight_fee ?? "-"}
                （{costTable.freight_basis === "weight" ? `按重量计费 ${costTable.weight_kg ?? 0} kg` : "按件计费"}）
              </p>
              {costTable.channels.sea && (
                <p>
                  海运成本明细：MC ${costTable.channels.sea.mc?.toFixed(2)}（采购+运费）｜P卡 ${costTable.channels.sea.packing_card?.toFixed(2)}｜分拣 ${costTable.channels.sea.sorting_fee?.toFixed(2)}｜佣金 ${costTable.channels.sea.referral_fee?.toFixed(2)}｜入库 ${costTable.channels.sea.inbound_fee?.toFixed(2)}｜仓储 ${costTable.channels.sea.storage_fee?.toFixed(2)}｜广告 ${costTable.channels.sea.ad_fee?.toFixed(2)}｜退货 ${costTable.channels.sea.return_loss?.toFixed(2)}｜附加 ${costTable.channels.sea.misc_fee?.toFixed(2)}｜超阈值 ${costTable.channels.sea.over_threshold_loss?.toFixed(2)}
                </p>
              )}
            </div>
          </>
        )}
      </div>
      {latestCalc?.id && (
        <div className="card mt-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">最新计算结果</h2>
            <Link href={`/calculation/${asin}`} className="text-sm" style={{ color: "var(--accent-blue)" }}>查看详情 →</Link>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div><p className="text-xs" style={{ color: "var(--text-tertiary)" }}>评分</p><p className="text-xl font-bold">{latestCalc.purchase_score ?? "-"}</p></div>
            <div><p className="text-xs" style={{ color: "var(--text-tertiary)" }}>级别</p><p className="text-xl font-bold">{latestCalc.purchase_level ?? "-"}</p></div>
            <div><p className="text-xs" style={{ color: "var(--text-tertiary)" }}>建议数量</p><p className="text-xl font-bold">{latestCalc.suggested_qty?.toLocaleString() ?? "-"}</p></div>
            <div><p className="text-xs" style={{ color: "var(--text-tertiary)" }}>库存天数</p><p className="text-xl font-bold">{latestCalc.inventory_days ?? "-"}</p></div>
          </div>
        </div>
      )}
    </div>
  );
}
