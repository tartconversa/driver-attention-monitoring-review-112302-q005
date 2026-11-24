"""持续导入：轮询数据目录，文件稳定后幂等导入。

只读取外部目录，不修改其中任何文件；同一批内容重复出现时按内容哈希跳过。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

from .importer import load_bundle
from .store import Store

WATCH_FILES = ("sessions.csv", "feedback.csv", "annotations.csv", "research_slots.csv")


def directory_fingerprint(data_dir: str | Path) -> str:
    """四个输入文件合并后的内容指纹。"""
    base = Path(data_dir)
    digest = hashlib.sha256()
    for name in WATCH_FILES:
        path = base / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def import_once(store: Store, data_dir: str | Path, actor: str = "ingest") -> dict[str, Any]:
    """单次导入：整批校验通过后写入；内容未变化时全部跳过。"""
    bundle = load_bundle(data_dir)
    return store.import_bundle(bundle, source_dir=str(data_dir), actor=actor)


def watch(
    store: Store,
    data_dir: str | Path,
    poll_interval: float = 2.0,
    max_cycles: int | None = None,
    actor: str = "ingest",
    sleep=time.sleep,
) -> list[dict[str, Any]]:
    """轮询直到目录消失或达到 max_cycles（测试可注入 sleep 与 max_cycles）。"""
    base = Path(data_dir)
    seen: set[str] = set()
    log: list[dict[str, Any]] = []
    cycles = 0
    while True:
        cycles += 1
        if max_cycles is not None and cycles > max_cycles:
            break
        if not base.exists():
            break
        try:
            fingerprint = directory_fingerprint(base)
        except FileNotFoundError:
            sleep(poll_interval)
            continue
        if fingerprint in seen:
            sleep(poll_interval)
            continue
        summary = import_once(store, base, actor=actor)
        seen.add(fingerprint)
        log.append({"fingerprint": fingerprint[:12], "summary": summary})
        sleep(poll_interval)
    return log
