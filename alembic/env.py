"""Alembic 迁移环境配置（Phase 1 / G-08：异步引擎 + 版本化迁移）

连接串来源优先级：
  1) 环境变量 ALEMBIC_DATABASE_URL（推荐：在宿主机执行迁移时指向 127.0.0.1:5433）
  2) app.config.settings.DATABASE_URL（容器内默认值）

用法：
  $env:ALEMBIC_DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/auto_replenishment"
  python -m alembic upgrade head
  python -m alembic stamp 0001_baseline     # 既有库首次纳入版本管理
"""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base
from app.models import *  # noqa: F401, F403  - 导入所有模型以便自动检测

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 只在未显式提供 URL 时回填，避免覆盖 alembic.ini / -x 传入的值
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option(
        "sqlalchemy.url",
        os.getenv("ALEMBIC_DATABASE_URL") or settings.DATABASE_URL,
    )

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连库"""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
