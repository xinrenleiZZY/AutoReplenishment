"""ASIN 语义分类表 - 存储 AI 对亚马逊标题的装饰品/非装饰品判定结果（以 ASIN 为主键）"""

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SemanticClassification(Base):
    __tablename__ = "semantic_classifications"

    asin: Mapped[str] = mapped_column(String(20), primary_key=True, comment="ASIN")
    listing_title: Mapped[str | None] = mapped_column(Text, comment="亚马逊标题(item_name)")
    semantic_classification: Mapped[str | None] = mapped_column(
        String(20), comment="语义分类（装饰品/非装饰品）"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
