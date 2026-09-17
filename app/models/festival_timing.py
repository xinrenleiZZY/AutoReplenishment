"""节日时间点表 — 存储解析后的结构化时间（支持自然语言时间描述）

从「更新版-产品生命周期时间点2026.7.30.xlsx」导入。
原始文本（可能为自然语言，如"3月末至4月初"）保留在 raw 字段，
解析结果（月/日区间、第N个星期X等）以 JSON 存于 *_json 字段。

解析结构（JSON 数组，一段文本可拆多段）：
  [{"type":"exact","year":2026,"month":5,"day":10},
   {"type":"month","month":11},
   {"type":"month_range","start_month":3,"end_month":8},
   {"type":"day_range","month":12,"start_day":1,"end_day":24},
   {"type":"range","start_month":9,"start_day":15,"end_month":10,"end_day":15},
   {"type":"nth_weekday","month":5,"week":2,"weekday":7},   # 第2个星期日
   {"type":"last_weekday","month":11,"weekday":4},          # 最后一个星期四
   {"type":"period","start":{"month":3,"part":"down"},"end":{"month":4,"part":"up"}},  # 月末至月初
   {"type":"whole_month","month":2},                        # 整个2月
   {"type":"unspecified","raw":"每年都不一样"}]
"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FestivalTiming(Base):
    __tablename__ = "festival_timing"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    festival: Mapped[str] = mapped_column(String(100), index=True, comment="节日/主题名称")

    # ── 原始文本（保留原文，含无法自动解析的自然语言） ──
    listing_start_raw: Mapped[str | None] = mapped_column(Text, comment="上架时间原始文本")
    festival_time_raw: Mapped[str | None] = mapped_column(Text, comment="节日/主题时间原始文本")
    festival_end_raw: Mapped[str | None] = mapped_column(Text, comment="结束时间原始文本")
    launch_raw: Mapped[str | None] = mapped_column(Text, comment="启动期原始文本")
    growth_raw: Mapped[str | None] = mapped_column(Text, comment="增长期原始文本")
    hot_raw: Mapped[str | None] = mapped_column(Text, comment="热卖期原始文本")
    mature_raw: Mapped[str | None] = mapped_column(Text, comment="成熟期原始文本")
    decline_raw: Mapped[str | None] = mapped_column(Text, comment="下降期原始文本")

    # ── 解析结果（JSON） ──
    listing_start_json: Mapped[str | None] = mapped_column(Text, comment="上架时间解析JSON")
    festival_time_json: Mapped[str | None] = mapped_column(Text, comment="节日时间解析JSON")
    festival_end_json: Mapped[str | None] = mapped_column(Text, comment="结束时间解析JSON")
    launch_json: Mapped[str | None] = mapped_column(Text, comment="启动期解析JSON")
    growth_json: Mapped[str | None] = mapped_column(Text, comment="增长期解析JSON")
    hot_json: Mapped[str | None] = mapped_column(Text, comment="热卖期解析JSON")
    mature_json: Mapped[str | None] = mapped_column(Text, comment="成熟期解析JSON")
    decline_json: Mapped[str | None] = mapped_column(Text, comment="下降期解析JSON")

    notes: Mapped[str | None] = mapped_column(Text, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
