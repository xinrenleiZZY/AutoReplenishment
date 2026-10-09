"""应用配置管理"""

from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # 应用
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    APP_NAME: str = "自动补货决策系统"  # 应用名称（前端据此展示）
    APP_VERSION: str = "3.9.17"        # 应用版本号（前端经 /api/v1/app-info 动态读取）

    # 数据库
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5433/auto_replenishment"

    # CORS（逗号分隔的来源白名单；默认 * 允许所有来源，不带凭证）
    CORS_ORIGINS: str = "*"

    # ── API 写操作鉴权（Phase 0 / G-04，可开关） ──
    # 为空 = 不鉴权（保持现状，零风险上电）；配置后，/api/v1/* 必须携带请求头 X-API-Token
    # 前端由 web/src/middleware.ts 在服务端注入该头，不暴露给浏览器
    API_AUTH_TOKEN: str = ""

    # 领星API
    LX_API_BASE_URL: str = ""
    LX_APP_KEY: str = ""
    LX_APP_SECRET: str = ""
    LX_COMPANY_ID: str = ""

    # ── 物流追踪实时库（FBA在途库存到货时间） ──
    LOGISTICS_API_URL: str = "http://192.168.0.193:7650"
    LOGISTICS_API_TOKEN: str = ""
    LOGISTICS_API_TIMEOUT: float = 15.0

    # ── 领星网页会话接口 headers（抽到 config/.env，便于后续填；空则回退脚本内兜底值）
    LX_AUTH_TOKEN: str = ""          # 领星网页 auth-token（本地直连兜底；服务站不可用时启用）
    LX_HEADER_AUTH_TOKEN: str = ""   # auth-token（兼容独立 LX_AUTH_TOKEN）
    LX_HEADER_COMPANY_ID: str = ""   # x-ak-company-id
    LX_HEADER_UID: str = ""          # x-ak-uid
    LX_HEADER_ENV_KEY: str = ""      # x-ak-env-key

    # ── 本地 CDP 兜底刷新（服务站不可用时，接入本机浏览器登录领星并捕获 auth-token） ──
    LX_USERNAME: str = ""            # 领星账号（CDP 自动登录）
    LX_PASSWORD: str = ""            # 领星密码
    LX_CDP_HOST: str = "127.0.0.1"   # CDP 浏览器主机（容器内用 host.docker.internal）
    LX_CDP_PORT: int = 18800         # CDP 调试端口

    # ── 领星 API 服务站（首选通道：登录态由服务端注入，摆脱本地 auth-token 顶号问题） ──
    LX_STATION_BASE_URL: str = ""     # 服务站地址，如 http://192.168.40.194:7788
    LX_STATION_USER_ID: str = ""      # 服务站账号
    LX_STATION_PASSWORD: str = ""     # 服务站密码
    LX_STATION_TIMEOUT: float = 600.0  # 单次 /api/proxy 超时秒数

    # 飞书通知（应用机器人方式优先）
    FEISHU_APP_ID: Optional[str] = None
    FEISHU_APP_SECRET: Optional[str] = None
    FEISHU_CHAT_ID: Optional[str] = None
    FEISHU_GROUP_WHITELIST: str = ""  # 日报推送群白名单（多个群ID用逗号分隔，优先于 FEISHU_CHAT_ID）
    # 自定义机器人 Webhook（备用）
    FEISHU_WEBHOOK_URL: Optional[str] = None

    # 定时任务
    SYNC_INTERVAL_HOURS: int = 24
    SYNC_TIME: str = "07:00"  # 数据同步每日执行时间（HH:MM，主数据全量同步窗口起点 07:00–08:20）
    CALC_INTERVAL_HOURS: int = 24
    DAILY_REPORT_TIME: str = "09:00"  # 按频率计算+日报时间（HH:MM）
    # 日报大屏图片生成：browser=用 headless Chromium 截取网页数据大屏（与网页完全一致，默认）；
    # matplotlib=旧版绘图兜底（网页不可达或关闭时自动回退，也可手动指定）
    REPORT_IMAGE_MODE: str = "browser"
    REPORT_IMAGE_URL: str = "http://auto_replenish_web:8000/board"  # API 容器内访问 web 服务
    REPORT_IMAGE_WIDTH: int = 1600
    REPORT_IMAGE_HEIGHT: int = 900
    REPORT_IMAGE_SCALE: float = 2.0
    REPORT_IMAGE_TIMEOUT_MS: int = 45000

    # 按等级计算频率（天），可自定义，格式: S:1,A:3,B:5,C:7,D:14
    CALC_FREQUENCIES: str = "S:1,A:3,B:5,C:7,D:14"
    # 无等级/未配置等级时的默认频率（天）
    CALC_FREQUENCY_DEFAULT: int = 14
    # 日报分析生命周期自定义（逗号分隔，可多选：启动期/增长期/热卖期/成熟期/下降期/未知；空=全部生命周期不过滤）
    REPORT_LIFECYCLES: str = ""
    # 产品等级口径：mixed=老品按去年总销量+新品按近30天年化（方案D，默认）；annualize=全部近30天年化（方案C）
    PRODUCT_LEVEL_MODE: str = "mixed"

    # 计算参数
    FORECAST_MONTHS: int = 6
    SAFE_STOCK_DAYS: int = 0
    SEA_SLOW_DAYS: int = 30
    SEA_PEAK_DAYS: int = 45
    AIR_SLOW_DAYS: int = 10
    AIR_PEAK_DAYS: int = 15
    EXPRESS_SLOW_DAYS: int = 3
    EXPRESS_PEAK_DAYS: int = 6

    # 评分平衡系数（内部调参：写入 .env 生效，不对外展示；默认1.0=不调整）
    # 各维度分数 × 对应系数后按系数总和归一化，保持总分 0-100
    SCORE_BALANCE_SHORTAGE: float = 1.0
    SCORE_BALANCE_TREND: float = 1.0
    SCORE_BALANCE_PROFIT: float = 1.0
    SCORE_BALANCE_LIFE: float = 1.0
    SCORE_BALANCE_URGENCY: float = 1.0
    SCORE_BALANCE_TRANSPORT: float = 1.0

    # 成本表单件附加费用（美元/件，JSON）：分拣费/入库配置费/仓储费/广告费/退货亏损/尾程杂费/附加费
    # 矫正三渠道Profit时逐项扣减；空=不扣
    COST_EXTRA_FEES_USD: str = ""

    # ---- 成本表（三渠道 Profit）模板参数：圣诞毛衣奖牌成本表 2026.7.30 口径 ----
    # Profit = 售价 - MC(采购总成本/汇率 + 运费) - P卡费 - 分拣费 - 佣金 - 入库配置费
    #          - 仓储费 - 广告费预算 - 退货成本亏损平摊 - 超阈值亏损 - 附加费
    COST_EXCHANGE_RATE: float = 6.5          # 成本表汇率（模板口径）
    COST_REFERRAL_RATIO: float = 0.15        # 平台佣金费率（佣金=MAX(1, 售价x费率)）
    COST_REFERRAL_MIN: float = 1.0           # 佣金最低 $1
    COST_PACKING_RATIO: float = 0.03         # P卡费 = (售价-分拣费-佣金)x3%
    COST_MISC_RATIO: float = 0.035           # 附加费 = 分拣费x3.5%
    COST_AD_RATIO: float = 0.17              # 广告费预算 = 售价x17%
    COST_RETURN_RATIO: float = 0.03          # 退货成本亏损平摊 = 售价x3%
    COST_INBOUND_FEE: float = 0.32           # 入库配置费 $/件
    COST_STORAGE_FEE: float = 0.1093         # 仓储费 $/件（无尺寸时的兜底）
    COST_RETURN_THRESHOLD: float = 0.087     # 类目退货阈值
    COST_OVER_THRESHOLD_FEE: float = 2.08    # 超过阈值的单套收费 $
    COST_DEFAULT_WEIGHT_KG: float = 0.0      # 默认计费重量kg；0=缺重量时按件计费

    # 库存紧急程度分档（天）：库存天数 < 危险 → 危险；< 偏低 → 偏低；<= 健康 → 健康；> 健康 → 过量
    INVENTORY_DANGER_MAX_DAYS: int = 15
    INVENTORY_LOW_MAX_DAYS: int = 30
    INVENTORY_HEALTHY_MAX_DAYS: int = 90

    # ── 新品补货策略（需求文档第六章） ──
    NEW_PRODUCT_ACOS_MAX: float = 0.55          # ACOS 硬性指标（≤55%）
    NEW_PRODUCT_MIN_SELLING_DAYS: int = 14      # 剩余售卖天数门槛（<14天终止）
    NEW_PRODUCT_TRIGGER_DAYS: int = 3           # 连续N天日均单量
    NEW_PRODUCT_TRIGGER_MIN_ORDER: float = 5.0  # 日均单量 > N 触发
    # 运费（人民币/件，按物流淡旺季；利润计算时按 USD_CNY_RATE 折算成美元）
    # 业务规则：淡季=便宜、旺季=贵；旺季=8-12月
    SEA_SLOW_FEE: float = 15.0
    SEA_PEAK_FEE: float = 17.0
    AIR_SLOW_FEE: float = 60.0
    AIR_PEAK_FEE: float = 70.0
    EXPRESS_SLOW_FEE: float = 70.0
    EXPRESS_PEAK_FEE: float = 80.0
    # 人民币兑美元汇率（运费换算用）
    USD_CNY_RATE: float = 7.2

    # 运营人员白名单（逗号分隔，仅保留这些负责人用于日报@；空=全部同步）
    OPERATOR_WHITELIST: str = ""

    # ── Listing 同步（新方式按创建时间，可切换） ──
    LISTING_SYNC_MODE: str = "full"        # full=全量（旧方式） / by_create_time=按创建时间区间
    LISTING_CREATE_START: str = ""         # 创建时间起始 YYYY-MM-DD（含）
    LISTING_CREATE_END: str = ""           # 创建时间结束 YYYY-MM-DD（含）
    LISTING_EXCLUDE_ASINS: str = ""        # ASIN 排除列表（逗号/换行分隔，强制停用不导入）
    LISTING_KEEP_ASINS: str = ""           # ASIN 保留列表（逗号/换行分隔，优先级高于排除，且不会被自动标记删除）
    # 节日产品缓冲天数（需求截止 = 节日日期 - 缓冲）
    DECORATION_BUFFER_DAYS: int = 14            # 装饰类
    NON_DECORATION_BUFFER_DAYS: int = 3         # 非装饰/DIY
    # 长期产品安全系数
    LONG_TERM_SAFETY_FACTOR: float = 1.2
    # 新品&长期产品等级评定安全系数（等级 = 基准 × 365 × 该值）
    LONG_TERM_LEVEL_SAFETY_FACTOR: float = 0.5

    # ── DeepSeek AI 评估（采购决策辅助） ──
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    DEEPSEEK_AI_EVAL_ENABLED: bool = False
    DEEPSEEK_TIMEOUT: float = 60.0
    AI_AUTO_EVALUATE: bool = True   # 计算时自动对需要决策的新老品做 AI 综合评估（当天幂等）

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
