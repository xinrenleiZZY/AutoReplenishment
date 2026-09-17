# -*- coding: utf-8 -*-
"""采购日报 + 重点可视化大屏图片生成（matplotlib）

供定时任务 / API 调用，将今日日报关键指标渲染为 16x9 深色大屏图，
再通过飞书应用机器人以图片消息发送到群。
"""
import io
import logging
from datetime import date

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

from sqlalchemy import text

from app.config import settings
from app.database import async_session_factory

logger = logging.getLogger(__name__)

# 中文字体（容器内安装 fonts-noto-cjk：Noto Sans CJK JP/SC/TC 均含中文字形；本地可用微软雅黑/黑体）
_cjk_font = None
for f in fm.fontManager.ttflist:
    if f.name.startswith("Noto Sans CJK") or f.name in ("Microsoft YaHei", "SimHei", "WenQuanYi Zen Hei"):
        _cjk_font = f.name
        break
if _cjk_font:
    plt.rcParams["font.sans-serif"] = [_cjk_font, "DejaVu Sans"]
else:
    plt.rcParams["font.sans-serif"] = ["DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
logger.info("日报大屏中文字体: %s", _cjk_font or "未找到（将乱码）")

BG = "#0b1220"
PANEL = "#111c33"
TEXT = "#e2e8f0"
SUB = "#94a3b8"
BLUE = "#3b82f6"
GREEN = "#10b981"
ORANGE = "#f59e0b"
RED = "#ef4444"
PURPLE = "#8b5cf6"


async def fetch_summary() -> list[dict]:
    """拉取今日计算结果 + 产品基础信息 + 同比月度销量

    数据口径与 get_daily_summary 对齐：优先取当日 logic_version=1 的结果，
    当日无新逻辑结果时回退“每个 ASIN 最近一次结果”；仅统计在售产品。
    """
    from app.tasks.calculation_tasks import normalize_level

    today = date.today()
    # 上月 / 去年上月（完整月）：同比基准应与“上月”同月、年份减一
    y, m = today.year, today.month
    if m > 1:
        last_m = f"{y}-{m - 1:02d}"
        yoy_base = f"{y - 1}-{m - 1:02d}"
    else:
        last_m = f"{y - 1}-12"
        yoy_base = f"{y - 2}-12"
    async with async_session_factory() as session:
        rows = (await session.execute(text(
            "SELECT asin, purchase_level, purchase_trigger, purchase_score, suggested_qty, "
            "inventory_days, replenishment_cycle, forecast_total "
            "FROM calculation_results WHERE calc_date = :d AND logic_version = 1"
        ), {"d": today})).mappings().all()
        if not rows:
            # 今日无新逻辑结果时取每个 ASIN 最近一次结果（与 get_daily_summary 回退口径一致）
            rows = (await session.execute(text(
                "SELECT r.asin, r.purchase_level, r.purchase_trigger, r.purchase_score, "
                "r.suggested_qty, r.inventory_days, r.replenishment_cycle, r.forecast_total "
                "FROM calculation_results r "
                "JOIN (SELECT asin, MAX(calc_date) d FROM calculation_results GROUP BY asin) t "
                "ON r.asin = t.asin AND r.calc_date = t.d "
                "JOIN products p ON p.asin = r.asin AND p.status = true"
            ))).mappings().all()
        prods = (await session.execute(text(
            "SELECT asin, product_name, product_level, thirty_volume, afn_fulfillable_quantity "
            "FROM products WHERE status = true"
        ))).mappings().all()
        hist = (await session.execute(text(
            "SELECT asin, month, sale_quantity FROM historical_monthly_stats "
            "WHERE source = 'lingxing' AND month IN (:a, :b)"
        ), {"a": last_m, "b": yoy_base})).mappings().all()
        # 生命周期分布 / 产品等级分布（与网页数据大屏口径一致）
        life_rows = (await session.execute(text(
            "SELECT life_cycle, COUNT(*) AS cnt FROM products WHERE status = true GROUP BY life_cycle"
        ))).mappings().all()
        lv_rows = (await session.execute(text(
            "SELECT product_level, COUNT(*) AS cnt FROM products WHERE status = true GROUP BY product_level"
        ))).mappings().all()
    pmap = {p["asin"]: p for p in prods}
    hy: dict[str, dict] = {}
    for h in hist:
        hy.setdefault(h["asin"], {})[h["month"]] = h["sale_quantity"] or 0
    results = []
    for r in rows:
        p = pmap.get(r["asin"])
        if not p:
            continue  # 非在售产品不计入（对齐 get_daily_summary 的在售过滤）
        results.append({
            "asin": r["asin"], "level": normalize_level(r["purchase_level"]),
            "trigger": r["purchase_trigger"],
            "score": r["purchase_score"], "qty": r["suggested_qty"] or 0,
            "days": r["inventory_days"], "cycle": r["replenishment_cycle"],
            "name": p.get("product_name") or "", "plv": p.get("product_level") or "",
            "vol30": p.get("thirty_volume") or 0, "stock": p.get("afn_fulfillable_quantity") or 0,
            "yoy_sales": hy.get(r["asin"], {}).get(last_m),
            "yoy_base": hy.get(r["asin"], {}).get(yoy_base),
        })
    life_dist = [{"label": r["life_cycle"] or "未知", "value": r["cnt"]} for r in life_rows]
    level_dist = [{"label": r["product_level"] or "未知", "value": r["cnt"]} for r in lv_rows]
    return {"results": results, "life_dist": life_dist, "level_dist": level_dist}


def build_fig(data: dict) -> plt.Figure:
    """渲染大屏图（与网页数据大屏图表一致：KPI + 评分分布 + 等级环形 + 生命周期分布 +
    断货/积压 TOP + 产品等级分布 + 同比对比 + 重点提醒）"""
    results = data["results"]
    life_dist = data.get("life_dist", [])
    level_dist = data.get("level_dist", [])
    total = len(results)
    cnt = {"立即采购": 0, "观察": 0, "暂停": 0, "未触发": 0, "终止": 0}
    for r in results:
        cnt[r["level"]] = cnt.get(r["level"], 0) + 1
    stockout = [r for r in results if r["trigger"] == "需要采购"]
    overstock = sorted(
        [r for r in results if r["days"] is not None and r["days"] >= 90],
        key=lambda x: -(x["days"] or 0),
    )[:8]
    alerts = sorted(stockout, key=lambda x: -(x["score"] or 0))[:10]
    # 评分分布
    buckets = [0] * 10
    for r in results:
        if isinstance(r["score"], (int, float)):
            buckets[min(int(r["score"]) // 10, 9)] += 1
    # 同比（有数据的 top ASIN）
    yoy_items = []
    for r in results:
        if r["yoy_sales"] is not None and r["yoy_base"] and r["yoy_base"] > 0:
            pct = round((r["yoy_sales"] - r["yoy_base"]) / r["yoy_base"] * 100, 1)
            yoy_items.append((r, pct))
    yoy_items.sort(key=lambda x: -abs(x[1]))
    yoy_items = yoy_items[:8]

    fig = plt.figure(figsize=(16, 9), dpi=130, facecolor=BG)
    gs = fig.add_gridspec(5, 6, hspace=0.45, wspace=0.35, left=0.03, right=0.97, top=0.88, bottom=0.05)

    # 标题
    fig.text(0.03, 0.94, f"自动补货决策日报　{date.today()}", fontsize=18, fontweight="bold", color=TEXT)
    fig.text(0.97, 0.94, f"扫描 ASIN {total}", fontsize=11, color=SUB, ha="right")

    # KPI 行
    kpis = [
        ("总 ASIN", total, BLUE), ("立即采购", cnt["立即采购"], RED), ("观察", cnt["观察"], ORANGE),
        ("暂停", cnt["暂停"], GREEN), ("断货风险", len(stockout), "#f97316"), ("库存积压", len(overstock), PURPLE),
    ]
    for i, (label, val, color) in enumerate(kpis):
        ax = fig.add_subplot(gs[0, i])
        ax.set_facecolor(PANEL)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.set_xticks([]); ax.set_yticks([])
        ax.text(0.5, 0.62, f"{val:,}", ha="center", fontsize=24, fontweight="bold", color=color)
        ax.text(0.5, 0.18, label, ha="center", fontsize=11, color=SUB)

    # 评分分布
    ax = fig.add_subplot(gs[1:3, 0:2])
    ax.set_facecolor(PANEL); ax.set_title("采购评分分布", color=TEXT, fontsize=12)
    x = range(10)
    ax.bar(x, buckets, color=BLUE, width=0.7)
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{i * 10}+" for i in x], fontsize=9, color=SUB)
    ax.tick_params(colors=SUB, labelsize=9)
    for i, v in enumerate(buckets):
        if v > 0:
            ax.text(i, v + max(buckets) * 0.01, str(v), ha="center", fontsize=9, color=TEXT)

    # 等级分布环形
    ax = fig.add_subplot(gs[1:3, 2])
    ax.set_facecolor(PANEL)
    sizes = [cnt["立即采购"], cnt["观察"], cnt["暂停"]]
    colors = [RED, ORANGE, GREEN]
    labels_lv = ["立即采购", "观察", "暂停"]
    ax.pie(sizes, colors=colors, startangle=90, counterclock=False,
           autopct=lambda p: f"{int(round(p * sum(sizes) / 100))}", textprops={"fontsize": 9, "color": "#fff"},
           wedgeprops={"width": 0.42, "edgecolor": BG})
    ax.set_title("采购等级分布", color=TEXT, fontsize=12)
    ax.legend(labels_lv, loc="center left", bbox_to_anchor=(1.0, 0.5), fontsize=9, frameon=False, labelcolor=SUB)

    # 断货 TOP
    ax = fig.add_subplot(gs[1:3, 4:6])
    ax.set_facecolor(PANEL); ax.axis("off"); ax.set_title("断货风险 TOP", color=RED, fontsize=12)
    text = "\n".join(
        f"{i + 1}. {a['asin']} [{a['plv']}] 库存{a['days']}天<周期{a['cycle']}　建议{a['qty']:,}件"
        for i, a in enumerate(alerts[:8])
    ) or "暂无断货风险"
    ax.text(0.02, 0.97, text, va="top", fontsize=10, color=TEXT, linespacing=1.7)

    # 生命周期分布（与网页大屏一致）
    ax = fig.add_subplot(gs[1:3, 3])
    ax.set_facecolor(PANEL); ax.set_title("生命周期分布", color=TEXT, fontsize=12)
    _dist_bar(ax, life_dist, color=PURPLE)

    # 产品等级分布（与网页大屏一致）
    ax = fig.add_subplot(gs[3:5, 0:2])
    ax.set_facecolor(PANEL); ax.set_title("产品等级分布（S/A/B/C/D）", color=TEXT, fontsize=12)
    _dist_bar(ax, level_dist, color="#06b6d4")

    # 积压 TOP
    ax = fig.add_subplot(gs[3:5, 4])
    ax.set_facecolor(PANEL); ax.axis("off"); ax.set_title("库存积压 TOP", color=ORANGE, fontsize=12)
    text = "\n".join(
        f"{i + 1}. {a['asin']}  {a['days']}天"
        for i, a in enumerate(overstock[:6])
    ) or "暂无积压"
    ax.text(0.02, 0.97, text, va="top", fontsize=10, color=TEXT, linespacing=1.7)

    # 同比对比（上月 vs 去年上月）
    ax = fig.add_subplot(gs[3:5, 2:4])
    ax.set_facecolor(PANEL); ax.set_title("同比对比 · 上月销量 vs 去年上月（TOP ASIN）", color=TEXT, fontsize=12)
    if yoy_items:
        names = [f"{r['asin']}\n({pct:+.0f}%)" for r, pct in yoy_items]
        cur = [r["yoy_sales"] for r, _ in yoy_items]
        base = [r["yoy_base"] for r, _ in yoy_items]
        x = range(len(yoy_items))
        w = 0.38
        ax.bar([i - w / 2 for i in x], base, width=w, label="去年上月", color="#475569")
        ax.bar([i + w / 2 for i in x], cur, width=w, label="上月", color=GREEN)
        ax.set_xticks(list(x))
        ax.set_xticklabels(names, fontsize=8, color=SUB)
        ax.legend(fontsize=9, frameon=False, labelcolor=SUB)
    else:
        ax.text(0.5, 0.5, "暂无同比数据", ha="center", color=SUB, fontsize=11)
    ax.tick_params(colors=SUB, labelsize=9)

    # 重点提醒 TOP
    ax = fig.add_subplot(gs[3:5, 5])
    ax.set_facecolor(PANEL); ax.axis("off"); ax.set_title("每日重点提醒 TOP", color=ORANGE, fontsize=12)
    rows_txt = []
    for i, a in enumerate(alerts[:7]):
        name = (a["name"] or "")[:14]
        lv = a["level"] or ""
        rows_txt.append(f"{i + 1}. {a['asin']} {name}　[{lv}] {a['qty']:,}件")
    ax.text(0.02, 0.97, "\n".join(rows_txt) or "暂无提醒", va="top", fontsize=9, color=TEXT, linespacing=1.8)

    fig.text(0.03, 0.02, "自动补货决策系统 · 数据大屏可视化", fontsize=9, color=SUB)
    return fig


def _dist_bar(ax, items: list[dict], color: str):
    """分布柱状图（生命周期/产品等级通用）"""
    if not items:
        ax.text(0.5, 0.5, "暂无数据", ha="center", color=SUB, fontsize=11)
        ax.tick_params(colors=SUB, labelsize=9)
        return
    labels = [it["label"] for it in items]
    vals = [it["value"] for it in items]
    n = len(labels)
    x = range(n)
    ax.bar(x, vals, color=color, width=0.7)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8, color=SUB, rotation=30, ha="right")
    ax.tick_params(colors=SUB, labelsize=9)
    for i, v in enumerate(vals):
        ax.text(i, v + max(vals) * 0.01, str(v), ha="center", fontsize=9, color=TEXT)


async def generate_image_bytes() -> bytes:
    """生成日报大屏 PNG 字节（优先截图网页数据大屏，失败回退 matplotlib 绘图）"""
    if str(getattr(settings, "REPORT_IMAGE_MODE", "browser")).lower() == "browser":
        try:
            return await _render_board_screenshot()
        except Exception as e:
            logger.warning("网页大屏截图失败，回退 matplotlib 绘图: %s", e)
    results = await fetch_summary()
    fig = build_fig(results)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight", dpi=130)
    plt.close(fig)
    return buf.getvalue()


async def _render_board_screenshot() -> bytes:
    """用 headless Chromium 截取网页数据大屏，版式与网页完全一致"""
    from playwright.async_api import async_playwright

    url = getattr(settings, "REPORT_IMAGE_URL", "") or "http://auto_replenish_web:8000/board"
    width = int(getattr(settings, "REPORT_IMAGE_WIDTH", 1600) or 1600)
    height = int(getattr(settings, "REPORT_IMAGE_HEIGHT", 900) or 900)
    scale = float(getattr(settings, "REPORT_IMAGE_SCALE", 2.0) or 2.0)
    timeout = int(getattr(settings, "REPORT_IMAGE_TIMEOUT_MS", 45000) or 45000)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--hide-scrollbars",
            ],
        )
        page = await browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        await page.goto(url, wait_until="networkidle", timeout=timeout)
        # 隐藏左侧导航栏、首屏加载遮罩（LoadingCover，z-[99999]）并恢复 body 滚动，
        # 截图只保留大屏内容，避免截到 AUTO_REPLENISH 进度动画
        await page.add_style_tag(content="""
            aside { display: none !important; }
            main { padding-left: 0 !important; }
            div[class*="z-[99999]"] { display: none !important; }
            body { overflow: auto !important; }
        """)
        # 等待数据加载完成：KPI 卡片渲染出数值 + 底部重点提醒区块出现
        await page.wait_for_function(
            """() => {
                const cards = [...document.querySelectorAll('.stat-card')];
                return cards.length >= 6 && cards.some(c => /\\d/.test(c.textContent || ''));
            }""",
            timeout=timeout,
        )
        await page.wait_for_selector("text=每日重点提醒 TOP", timeout=timeout)
        # 兜底：移除加载遮罩元素并恢复滚动，防止样式注入失效
        await page.evaluate(
            """() => {
                document.querySelectorAll('div[class*="z-[99999]"]').forEach(el => el.remove());
                document.body.style.overflow = 'auto';
            }"""
        )
        await page.wait_for_timeout(800)
        shot = await page.screenshot(full_page=True)
        await browser.close()
        return shot


async def generate_and_send(notifier=None) -> bool:
    """生成日报大屏图片并发送到飞书群（供定时任务/API 复用）"""
    if notifier is None:
        from app.integrations.feishu import FeishuNotifier
        notifier = FeishuNotifier()
    try:
        image_bytes = await generate_image_bytes()
        ok = await notifier.send_image(image_bytes)
        if ok:
            logger.info("日报大屏图片已发送到飞书")
        else:
            logger.warning("日报大屏图片发送失败")
        return ok
    except Exception as e:
        logger.error(f"生成/发送日报大屏图片异常: {e}")
        return False
