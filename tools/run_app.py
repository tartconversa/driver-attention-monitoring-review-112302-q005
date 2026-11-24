"""命令行启动器：在隔离模式（python -I）下显式加入 src 路径。

用法：
    python3 -I tools/run_app.py <子命令> ...
所有资料路径均通过命令参数传入，脚本不依赖当前工作目录。
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from driver_attention_monitoring_review.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
