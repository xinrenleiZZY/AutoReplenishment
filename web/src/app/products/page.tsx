"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, type Product } from "@/lib/api";

// 等级徽标样式（S爆款→A→B→C→D）
const LEVEL_STYLE: Record<string, { bg: string; color: string }> = {
  S: { bg: "#fef3c7", color: "#92400e" },
  A: { bg: "#dbeafe", color: "#1e40af" },
  B: { bg: "#dcfce7", color: "#166534" },
  C: { bg: "#f1f5f9", color: "#334155" },
  D: { bg: "#f1f5f9", color: "#64748b" },
};

export default function ProductsPage() {
  const router = useRouter();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [keyword, setKeyword] = useState("");
  const [debounced, setDebounced] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setDebounced(keyword.trim()), 300);
    return () => clearTimeout(t);
  }, [keyword]);

  const loadProducts = useCallback(() => {
    setLoading(true);
    api.products
      .list({ limit: 200, keyword: debounced || undefined })
      .then(setProducts)
      .catch(() => setProducts([]))
      .finally(() => setLoading(false));
  }, [debounced]);

  useEffect(() => { loadProducts(); }, [loadProducts]);

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">产品列表</h1>
        <input
          type="text"
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          placeholder="搜索 ASIN / 名称 / 分类..."
          className="text-sm px-3 py-1.5 rounded-md border w-64"
          style={{ backgroundColor: "var(--bg-secondary)", borderColor: "var(--border-color)", color: "var(--text-primary)" }}
        />
      </div>
      <p className="text-sm mb-4" style={{ color: "var(--text-tertiary)" }}>
        {debounced ? `搜索 "${debounced}"：` : ""}共 {products.length} 个产品
      </p>
      {loading && <p>加载中...</p>}
      {!loading && products.length === 0 && <div className="card text-center py-12" style={{ color: "var(--text-tertiary)" }}>未找到产品</div>}
      {!loading && products.length > 0 && (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr style={{ borderBottom: "1px solid var(--border-color)" }}>
              <th className="text-left py-2 pr-3">ASIN</th><th className="text-left py-2 pr-3">产品名称</th>
              <th className="text-left py-2 pr-3">分类</th><th className="text-center py-2 pr-3">等级</th>
              <th className="text-center py-2 pr-3">计算频率</th><th className="text-center py-2 pr-3">生命周期</th>
              <th className="text-center py-2">状态</th>
            </tr></thead>
            <tbody>{products.map(p => {
              const ls = LEVEL_STYLE[p.product_level || ""] || LEVEL_STYLE.D;
              return (
              <tr key={p.asin} style={{ borderBottom: "1px solid var(--border-color)" }}
                onClick={() => router.push(`/products/${p.asin}`)} className="cursor-pointer"
                onMouseEnter={e => e.currentTarget.style.backgroundColor = "var(--hover-bg)"}
                onMouseLeave={e => e.currentTarget.style.backgroundColor = "transparent"}>
                <td className="py-2 pr-3 font-mono text-xs">{p.asin}</td>
                <td className="py-2 pr-3 truncate max-w-xs">{p.product_name}</td>
                <td className="py-2 pr-3 text-xs">{p.category ?? "-"}</td>
                <td className="py-2 pr-3 text-center">{p.product_level && <span className="px-1.5 py-0.5 rounded text-xs font-bold" style={{ backgroundColor: ls.bg, color: ls.color }}>{p.product_level}</span>}</td>
                <td className="py-2 pr-3 text-center">{p.calc_frequency && <span className="px-1.5 py-0.5 rounded text-xs font-medium" style={{ backgroundColor: "var(--bg-tertiary)" }}>{p.calc_frequency}</span>}</td>
                <td className="py-2 pr-3 text-center text-xs">{p.life_cycle ?? "-"}</td>
                <td className="py-2 text-center"><span className={`inline-block w-2 h-2 rounded-full ${p.status ? "bg-green-500" : "bg-red-400"}`} /></td>
              </tr>
              );
            })}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
