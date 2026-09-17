"""listing 标签 → 节日 映射表（替代 tag_festival.py 硬编码字典，可后台/前端维护）"""

from datetime import datetime
from sqlalchemy import String, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TagFestivalMap(Base):
    __tablename__ = "tag_festival_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tag: Mapped[str] = mapped_column(String(50), unique=True, comment="listing 标签（领星 getRelationTagList）")
    festival: Mapped[str] = mapped_column(String(100), comment="系统节日名")
    category: Mapped[str] = mapped_column(String(20), default="festival", comment="festival=具体节日 / season=季节类")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间")
