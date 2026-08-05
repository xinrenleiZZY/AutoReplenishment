"""以分离进程方式启动全量批量计算，输出写入 logs/batch_calc_test.*.log

用法:
    python -m scripts.launch_batch_calc
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

stdout = (LOG_DIR / "batch_calc_test.log").open("wb", 0)
stderr = (LOG_DIR / "batch_calc_test.err.log").open("wb", 0)

proc = subprocess.Popen(
    [sys.executable, "-m", "scripts.trigger_batch_calculation"],
    cwd=str(ROOT),
    stdout=stdout,
    stderr=stderr,
    stdin=subprocess.DEVNULL,
    creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    close_fds=True,
)
print(proc.pid)
