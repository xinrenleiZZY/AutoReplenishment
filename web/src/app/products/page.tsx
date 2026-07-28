"use client";

import { useEffect, useState } from "react";

interface Product { asin: string; product_name: string; category: string | null; product_level: string | null; life_cycle: string | null; product_stage: string | null; festival: string | null; list_date: string | null; status: boolean; }

export default function ProductsPage() {
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch("/api/v1/products/?limit=200").then(r => r.json()).then(setProducts).catch(() => setProducts([])).finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">产品列表</h1>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>共 {products.length} 个产品</p>
      {loading && <p>加载中...</p>}
      {!loading && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">ASIN</th><th className="text-left py-2 pr-3">产品名称</th>
              <th className="text-left py-2 pr-3">分类</th><th className="text-center py-2 pr-3">等级</th>
              <th className="text-center py-2 pr-3">生命周期</th><th className="text-center py-2">状态</th>
            </tr></thead>
            <tbody>{products.map(p => (
              <tr key={p.asin} style={{ borderBottom: "1px solid var(--border-color)" }}
                onClick={() => window.location.href = `/products/${p.asin}`} className="cursor-pointer"
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}>
                <td className="py-2 pr-3 font-mono text-xs">{p.asin}</td>
                <td className="py-2 pr-3 truncate max-w-xs">{p.product_name}</td>
                <td className="py-2 pr-3 text-xs">{p.category ?? "-"}</td>
                <td className="py-2 pr-3 text-center">{p.product_level && <span className="px-1.5 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: p.product_level === "S" ? "#fef3c7" : "#f1f5f9" }}>{p.product_level}</span>}</td>
                <td className="py-2 pr-3 text-center text-xs">{p.life_cycle ?? "-"}</td>
                <td className="py-2 text-center"><span className={`inline-block w-2 h-2 rounded-full ${p.status ? "bg-green-500" : "bg-red-400"}`} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
