# -*- coding: utf-8 -*-
"""
产品 → 节日自动匹配脚本
策略：用产品英文名称关键词匹配 53 个节日/主题 → 回填 products.festival

匹配优先级：关键词长度降序（先匹配"4th of july"再匹配"july"），避免短词误匹配
用法: python scripts/assign_festival.py [--dry-run]
"""
import asyncio
import re
import sys
from collections import Counter

sys.path.insert(0, r"e:\ZY2026\yy021-自动补货决策系统")

from sqlalchemy import select
from app.database import async_session_factory
from app.models.product import Product

# 节日 → 英文关键词列表（小写匹配；关键词越长优先匹配）
FESTIVAL_KEYWORDS = {
    "新年": ["new year", "happy new year"],
    "超级碗": ["super bowl", "superbowl"],
    "情人节": ["valentine", "valentines", "love bug", "love you", "heart", "cupid"],
    "狂欢节": ["mardi gras", "carnival", "masquerade"],
    "三叶草节": ["st patrick", "st patty", "shamrock", "clover"],
    "西部牛仔主题": ["cowboy", "western", "rodeo"],
    "露营主题": ["camping", "campsite", "camp fire", "campfire"],
    "复活节": ["easter", "bunny", "bunnies", "eggs hunt", "egg hunt", "jelly bean"],
    "墨西哥节": ["cinco de mayo", "mexican"],
    "肯塔基赛马节": ["kentucky derby", "derby"],
    "谢师周": ["teacher appreciation", "teacher"],
    "护士周": ["nurse appreciation", "nurse"],
    "母亲节": ["mother's day", "mothers day", "mom"],
    "毕业季": ["graduation", "grad"],
    "父亲节": ["father's day", "fathers day", "dad"],
    "国庆节": ["4th of july", "fourth of july", "independence day", "patriotic", "usa flag", "american flag", "star spangled"],
    "返校季": ["back to school", "school bus", "school supplies"],
    "万圣节": ["halloween", "vampire", "fangs", "spider", "pumpkin", "ghost", "skull", "witch", "skeleton", "zombie", "bat", "pirate", "superhero", "princess"],
    "亡灵节": ["day of the dead", "dia de los muertos", "sugar skull"],
    "感恩节": ["thanksgiving", "turkey"],
    "圣诞节": ["christmas", "xmas", "snowflake", "santa", "reindeer", "snowman", "ornament", "jingle", "merry"],
    "夏季类": ["summer", "beach", "pool party", "ocean", "seashell", "flamingo"],
    "春季类": ["spring", "butterfly"],
    "秋季类": ["fall", "autumn", "harvest", "acorn", "leaf"],
    "冬季类": ["winter", "snow"],
    "乳腺癌意识月": ["breast cancer", "pink ribbon"],
    "儿童癌症意识月": ["childhood cancer", "gold ribbon"],
    "同性恋月": ["pride", "lgbt", "rainbow"],
    "六月节": ["juneteenth"],
    "西班牙裔传统月": ["hispanic"],
    "啤酒节": ["oktoberfest", "beer"],
    "开学第100天": ["100th day", "100 days of school"],
    "降临节": ["advent"],
    "癫痫意识": ["epilepsy"],
    "糖尿病意识月": ["diabetes"],
    "心理健康意识月": ["mental health", "mindfulness"],
    "调度员感谢周": ["dispatcher"],
    "牙科助理周": ["dental assistant", "dentist", "tooth"],
    "社会工作者月": ["social worker"],
    "妇女历史月": ["women's history", "womens history"],
    "黑人历史月": ["black history"],
    "儿童虐待防治月": ["child abuse"],
    "家庭暴力意识月": ["domestic violence"],
    "预防自杀意识月": ["suicide prevention"],
    "美国药剂师月": ["pharmacist", "pharmacy"],
    "职业治疗": ["occupational therapy"],
    "阿尔茨海默症": ["alzheimer"],
    "基督教主题": ["christian", "jesus", "bible", "religious"],
}

# 收集关键词去重排序
def _build_matchers():
    matchers = []
    for festival, kws in FESTIVAL_KEYWORDS.items():
        for kw in kws:
            matchers.append((len(kw), kw, festival))
    # 按关键词长度降序（先长词后短词）
    matchers.sort(key=lambda x: -x[0])
    return matchers


MATCHERS = _build_matchers()


def match_festival(product_name: str) -> str | None:
    """用产品名匹配节日，返回节日名或 None"""
    if not product_name:
        return None
    name = product_name.lower()
    for _, kw, festival in MATCHERS:
        # 用单词边界匹配（词首/词尾 或 前后空格）
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", name):
            return festival
    return None


async def main():
    dry_run = "--dry-run" in sys.argv
    s = async_session_factory()
    try:
        async with s:
            products = (await s.execute(
                select(Product).where(Product.product_name.isnot(None))
            )).scalars().all()
            print(f"待匹配产品: {len(products)}")

            matched = 0
            counter = Counter()
            unmatched_names = []
            for p in products:
                f = match_festival(p.product_name)
                if f:
                    matched += 1
                    counter[f] += 1
                    if not dry_run:
                        p.festival = f
            if not dry_run:
                await s.commit()

            print(f"匹配成功: {matched} ({matched / len(products) * 100:.1f}%)")
            print("\n=== 节日分布 TOP25 ===")
            for f, n in counter.most_common(25):
                print(f"  {f}: {n}")
            print(f"\n未匹配: {len(products) - matched}")
    finally:
        await s.close()


if __name__ == "__main__":
    asyncio.run(main())
