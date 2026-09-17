# -*- coding: utf-8 -*-
"""领星/SIF API 请求原始数据归档（完整原始响应入库）

记录所有领星网页 API / MCP / SIF 请求的完整原始响应，作为数据留存与排查依据。
"""

from datetime import date, datetime

from sqlalchemy import JSON, BigInteger, Date, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ApiRawResponse(Base):
    __tablename__ = "api_raw_responses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(80), index=True, comment="接口/来源标识")
    asin: Mapped[str | None] = mapped_column(String(20), index=True, comment="ASIN（按ASIN调用时）")
    request_url: Mapped[str | None] = mapped_column(Text, comment="请求地址")
    request_method: Mapped[str | None] = mapped_column(String(10), comment="请求方法")
    request_params: Mapped[dict | None] = mapped_column(JSON, comment="请求参数")
    response_raw: Mapped[dict | None] = mapped_column(JSON, comment="完整原始响应")
    status_code: Mapped[int | None] = mapped_column(Integer, comment="HTTP状态码")
    error: Mapped[str | None] = mapped_column(Text, comment="错误信息")
    fetch_date: Mapped[date] = mapped_column(Date, index=True, comment="抓取日期")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="入库时间")
