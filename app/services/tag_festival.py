# -*- coding: utf-8 -*-
"""listing 标签 → 节日 映射（默认种子 + 数据库覆盖 + 节日时间点表兜底）

- 默认映射写在 DEFAULT_TAG_FESTIVAL / DEFAULT_SEASON_TAGS，供首次建库种子与表为空时回退；
- 运行期优先读数据库表 tag_festival_map（scripts/sync_tag_festival_map.py 可一键同步默认值）；
- 标签还可直接命中 festival_calendar 表内的节日名（前端生命周期页新增节日后分类即时生效）。
"""

from sqlalchemy import select

from app.models.festival_calendar import FestivalCalendar
from app.models.tag_festival_map import TagFestivalMap

# 具体节日标签 → 系统节日名（优先匹配；默认种子）
DEFAULT_TAG_FESTIVAL = {
    "圣诞节": "圣诞节",
    "万圣节": "万圣节",
    "情人节": "情人节",
    "返校季": "返校季",
    "国庆节": "国庆节",
    "三叶草": "三叶草节",
    "毕业季": "毕业季",
    "复活节": "复活节",
    "感恩节": "感恩节",
    "基督教主题": "基督教主题",
    "狂欢节": "狂欢节",
    "fiesta": "fiesta",
    "嘉年华": "狂欢节",
    "同性恋": "同性恋月",
    "亡灵节": "亡灵节",
    "母亲节": "母亲节",
    "父亲节": "父亲节",
    "啤酒节": "啤酒节",
    "乳腺癌意识月": "乳腺癌意识月",
    "赛马节": "肯塔基赛马节",
    "六月节": "六月节",
    "谢师周": "谢师周",
    "黑人历史月": "黑人历史月",
    "超级碗": "超级碗",
    "露营主题": "露营主题",
    "西班牙裔传统月": "西班牙裔传统月",
    "调度员": "调度员感谢周",
    "家庭暴力意识月": "家庭暴力意识月",
    "儿童癌症意识": "儿童癌症意识月",
    "妇女历史月": "妇女历史月",
    "心理健康意识": "心理健康意识月",
    "牙科助理周": "牙科助理周",
    "新年": "新年",
    "糖尿病意识": "糖尿病意识月",
    "宽扎节": "宽扎节",
    "职业治疗": "职业治疗",
    "儿童虐待防治": "儿童虐待防治月",
    "阿尔茨海默症": "阿尔茨海默症",
    "降临节": "降临节",
    "美国药剂师月": "美国药剂师月",
    "癫痫意识": "癫痫意识",
    "开学第100天": "开学第100天",
    "茶会": "茶会",
}

# 季节类标签（无具体节日，仍按季节归类；默认种子）
DEFAULT_SEASON_TAGS = {
    "春季类": "春季类",
    "夏季类": "夏季类",
    "秋季类": "秋季类",
    "冬季": "冬季类",
}

# 长期产品特例标签（默认：长期、西部牛仔；可在参数表 product_type_long_tags 维护）
LONG_TAGS_DEFAULT = "长期,西部牛仔"


async def load_festival_maps(session) -> tuple[dict, dict]:
    """从 tag_festival_map 表加载 (festival_map, season_map)；表为空时回退默认种子"""
    rows = (await session.execute(select(TagFestivalMap))).scalars().all()
    if not rows:
        return dict(DEFAULT_TAG_FESTIVAL), dict(DEFAULT_SEASON_TAGS)
    festival_map, season_map = {}, {}
    for r in rows:
        (season_map if r.category == "season" else festival_map)[r.tag] = r.festival
    return festival_map, season_map


async def load_calendar_names(session) -> set:
    """festival_calendar 表内全部节日名（前端维护，分类命中即节日产品）"""
    rows = (await session.execute(select(FestivalCalendar.festival))).scalars().all()
    return {n for n in rows if n}


async def upsert_tag_map(session, tag: str, festival: str, category: str = "festival") -> bool:
    """写入标签映射：tag 已存在时不覆盖（保留自定义映射），返回是否新增。

    生命周期页新增/编辑节日时调用，保证 tag_festival_map 与 festival_calendar 同步。
    """
    tag = (tag or "").strip()
    festival = (festival or "").strip()
    if not tag:
        return False
    row = (await session.execute(
        select(TagFestivalMap).where(TagFestivalMap.tag == tag)
    )).scalar_one_or_none()
    if row is not None:
        return False
    session.add(TagFestivalMap(tag=tag, festival=festival, category=category))
    return True


def parse_tags(tags) -> list:
    """把 tags（字符串 JSON 数组 / 逗号分隔 / 列表）规整为标签列表"""
    if not tags:
        return []
    if isinstance(tags, str):
        import json

        raw = tags.strip()
        if raw.startswith("["):
            try:
                tags = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                tags = [t.strip() for t in raw.strip("[]").split(",") if t.strip()]
        else:
            tags = [t.strip() for t in raw.split(",") if t.strip()]
    if not isinstance(tags, (list, tuple, set)):
        return []
    return [str(t).strip() for t in tags if str(t).strip()]


def festival_from_tags(tags, festival_map: dict | None = None, season_map: dict | None = None) -> str | None:
    """从 listing 标签中提取节日：先具体节日，再季节类；无匹配返回 None"""
    tags = parse_tags(tags)
    if not tags:
        return None
    festival_map = festival_map or DEFAULT_TAG_FESTIVAL
    season_map = season_map or DEFAULT_SEASON_TAGS
    for tag in tags:
        if tag in festival_map:
            return festival_map[tag]
    for tag in tags:
        if tag in season_map:
            return season_map[tag]
    return None


def classify_product(
    tags,
    long_tags: str | None = None,
    festival_tags: str | None = None,
    festival_map: dict | None = None,
    season_map: dict | None = None,
    calendar_names: set | None = None,
):
    """按 listing 标签判定 产品类型 + 节日

    规则（优先级从高到低）：
      0. 无任何标签 → 未分类（festival 空，不判长期产品，避免被固定为热卖期）；
      1. 标签命中「长期产品特例」列表（参数 product_type_long_tags，默认含 长期、西部牛仔）
         → 长期产品，节日清空；
      2. 标签命中「节日产品特例」列表（参数 product_type_festival_tags）→ 节日产品，
         节日取标签映射，无映射用标签本身；
      3. 标准节日/季节标签（数据库 tag_festival_map，缺省用默认种子）→ 节日产品；
      4. 标签与 festival_calendar 表内节日名一致（前端新增节日即时生效）→ 节日产品；
      5. 其余 → 长期产品。
    返回 (festival, product_type)
    """
    tag_list = parse_tags(tags)
    if not tag_list:
        return None, "未分类"
    long_set = {t.strip() for t in str(long_tags or LONG_TAGS_DEFAULT).split(",") if t.strip()}
    fest_set = {t.strip() for t in str(festival_tags or "").split(",") if t.strip()}
    festival_map = festival_map or DEFAULT_TAG_FESTIVAL
    season_map = season_map or DEFAULT_SEASON_TAGS
    calendar_names = calendar_names or set()

    # 1) 长期产品特例：命中即长期（有长期就是长期产品）
    if any(t in long_set for t in tag_list):
        return None, "长期产品"
    # 2) 节日产品特例标签
    for tag in tag_list:
        if tag in fest_set:
            return festival_map.get(tag) or tag, "节日产品"
    # 3) 标准节日/季节标签
    fest = festival_from_tags(tag_list, festival_map, season_map)
    if fest:
        return fest, "节日产品"
    # 4) festival_calendar 表内节日名（前端维护，即时同步）
    for tag in tag_list:
        if tag in calendar_names:
            return tag, "节日产品"
    return None, "长期产品"
