# API 服务镜像（FastAPI + Uvicorn）
# 注意：保持单 worker 运行，避免 APScheduler 定时任务在多个 worker 中重复执行
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 健康检查与时区
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# 先安装依赖，利用 Docker 层缓存（requirements 不变时不重装）
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 复制运行所需代码（scripts/scraper 供每日同步任务使用）
COPY app ./app
COPY api ./api
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY scraper ./scraper
COPY utils ./utils
COPY .mcp.json ./

# 非 root 运行
RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/p_id \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]
