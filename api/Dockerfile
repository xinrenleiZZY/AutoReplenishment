# API 服务镜像（FastAPI + Uvicorn）
# 注意：保持单 worker 运行，避免 APScheduler 定时任务在多个 worker 中重复执行
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 健康检查与时区 + 中文字体（日报大屏图片渲染）
# apt 源默认 deb.debian.org 在部分网络不可达，切换国内镜像（中科大，阿里云在本机偶发龟速）
RUN sed -i 's|deb.debian.org|mirrors.ustc.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null; \
    printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\n' > /etc/apt/apt.conf.d/99retry; \
    apt-get update \
    && apt-get install -y --no-install-recommends curl tzdata fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

WORKDIR /app

# 先安装依赖，利用 Docker 层缓存（requirements 不变时不重装）；pip 用清华镜像
COPY requirements.txt ./
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
# 离线注入浏览器二进制（复用已下载的 chromium/headless shell/ffmpeg，headless shell 国内镜像缺失无法在线下载）
COPY ms-playwright /ms-playwright
# chromium 运行系统依赖手动安装（基础镜像为 bookworm，不含 t64 改名的包）
RUN apt-get update \
    && apt-get install -y --no-install-recommends --ignore-missing \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libdbus-1-3 libxcb1 libxkbcommon0 libx11-6 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2 libatspi2.0-0 libxshmfence1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# 复制运行所需代码（scripts/scraper 供每日同步任务使用；browser_api 供鉴权失败时自动刷新登录态）
COPY app ./app
COPY api ./api
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts
COPY scraper ./scraper
COPY utils ./utils
COPY browser_api ./browser_api
COPY .mcp.json ./

# 非 root 运行
RUN useradd --create-home --uid 1001 appuser \
    && mkdir -p /app/p_id \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /ms-playwright
USER appuser

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8001/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]
