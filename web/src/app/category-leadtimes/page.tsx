"use client";

export default function CategoryLeadtimesPage() {
  const categories = [
    { l1: "PVC类", items: ["充气类(15-20天)", "pvc印刷(15-20天)"] },
    { l1: "板类", items: ["板类桌面装饰(15-20天)", "板类挂饰(15-20天)", "木质套装(15-20天)"] },
    { l1: "布料类", items: ["布料套装(15-20天)", "背景布(15-20天)", "对联(7-10天)", "亚麻(7-10天)", "涤纶(15天)"] },
    { l1: "毛毡类", items: ["毛毡类(10-15天)"] },
    { l1: "圣诞花/礼物盒", items: ["枫叶(40-50天)", "花环(25-30天)", "花束(30-40天)", "礼物盒(10-15天)"] },
    { l1: "塑料类", items: ["头箍(15-20天)", "硅胶(10-25天)", "篮子(10-15天)", "亚克力(12-15天)"] },
    { l1: "纸质印刷", items: ["贴纸(12天)", "窗贴(15-20天)", "涂色本(15-20天)", "纸巾(15-20天)"] },
    { l1: "五金/灯串/珠串", items: ["五金(15-20天)", "灯串(15-20天)", "珠串(15-30天)"] },
  ];

  return (
    <div>
      <h1 className="text-2xl font-bold mb-2">产品分类工期</h1>
      <p className="text-sm mb-6" style={{ color: "var(--text-tertiary)" }}>60条分类工期记录（来自种子数据）</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {categories.map(cat => (
          <div key={cat.l1} className="card">
            <h3 className="font-semibold text-sm mb-2">{cat.l1}</h3>
            <ul className="space-y-1">{cat.items.map((item, i) => <li key={i} className="text-xs" style={{ color: "var(--text-secondary)" }}>{item}</li>)}</ul>
          </div>
        ))}
      </div>
    </div>
  );
}
