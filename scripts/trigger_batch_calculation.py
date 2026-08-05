"""手动触发一次全量批量计算（用于测试日报链路）

用法:
    python -m scripts.trigger_batch_calculation
"""

import asyncio
import logging

from app.tasks.calculation_tasks import run_batch_calculation

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


async def main():
    stats = await run_batch_calculation()
    print("BATCH_DONE", stats)


if __name__ == "__main__":
    asyncio.run(main())
