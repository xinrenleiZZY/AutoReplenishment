"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

export default function ProductDetailPage() {
  const params = useParams();
  const asin = params.asin as string;
  const [product, setProduct] = useState<any>(null);
  const [latestCalc, setLatestCalc] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetch(`/api/v1/products/${asin}`).then(r => r.json()),
      fetch(`/api/v1/calculation/results/${asin}/latest`).then(r => r.json().catch(() => null)),
    ]).then(([prod, calc]) => { setProduct(prod); setLatestCalc(calc); }).finally(() => setLoading(false));
  }, [asin]);

  if (loading) return <p>加载中...</p>;
  if (!product) return <div className="card text-center py-12">产品不存在</div>;

  const Row = ({ label, value }: { label: string; value: React.ReactNode }) => (
    <div className="flex py-2" style={{ borderBottom: "1px solid var(--border-color)" }}>
      <span className="w-36 text-sm" style={{ color: "var(--text-tertiary)" }}>{label}</span>
      <span className="text-sm">{value}</span>
    </div>
  );

  return (
    <div>
      <Link href="/products" className="text-sm mb-4 inline-block" style={{ color: "var(--accent-blue)" }}>← 返回产品列表</Link>
      <h1 className="text-2xl font-bold mb-6">{product.product_name}</h1>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">基础信息</h2>
          <Row label="ASIN" value={<span className="font-mono">{product.asin}</span>} />
          <Row label="分类" value={product.category || "-"} />
          <Row label="子分类" value={product.sub_category || "-"} />
          <Row label="所属节日" value={product.festival || "-"} />
          <Row label="产品类型" value={product.product_type || "-"} />
          <Row label="运营" value={product.operator || "-"} />
          <Row label="上架日期" value={product.list_date || "-"} />
          <Row label="利润率" value={product.profit_rate != null ? `${(product.profit_rate * 100).toFixed(0)}%` : "-"} />
        </div>
        <div className="card">
          <h2 className="text-lg font-semibold mb-4">生命周期与等级</h2>
          <Row label="阶段" value={<span className="px-2 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: product.product_stage === "新品" ? "#dbeafe" : "#f1f5f9" }}>{product.product_stage || "-"}</span>} />
          <Row label="生命周期" value={product.life_cycle || "-"} />
          <Row label="等级" value={product.product_level ? <span className="px-2 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: product.product_level === "S" ? "#fef3c7" : "#f1f5f9" }}>{product.product_level}</span> : "-"} />
          <Row label="工期(天)" value={product.lead_time ?? "-"} />
          <Row label="单箱数量" value={product.box_quantity ?? "-"} />
          <Row label="最低采购量" value={product.min_order_qty ?? "-"} />
          <Row label="状态" value={<span className={`inline-block w-2 h-2 rounded-full ${product.status ? "bg-green-500" : "bg-red-400"}`} />} />
        </div>
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
