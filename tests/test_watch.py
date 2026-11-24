"""目录轮询持续导入测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from _helpers import make_session, temp_dir
from tests_support import write_bundle

from driver_attention_monitoring_review.store import Store
from driver_attention_monitoring_review.watch import watch


class WatchTests(unittest.TestCase):
    def test_watch_imports_each_distinct_fingerprint_once(self):
        with temp_dir() as tmp:
            base = Path(tmp)
            data = base / "data"
            state = base / "state"
            data.mkdir()
            write_bundle(
                data,
                [make_session("S-001", "D-1")],
                [], [], [],
            )
            store = Store.create(state)

            ticks = iter([0, 0, 0, 0])  # 最多 4 个轮询周期

            def fake_sleep(_interval):
                try:
                    next(ticks)
                except StopIteration:
                    raise AssertionError("轮询未在预期周期内收敛")

            log = watch(store, data, poll_interval=0, max_cycles=4,
                        sleep=fake_sleep)
            # 指纹只出现一次 => 只导入一次
            self.assertEqual(len(log), 1)
            self.assertEqual(log[0]["summary"]["sessions"]["new"], 1)
            self.assertEqual(len(store.sessions()), 1)

    def test_watch_picks_up_new_files_after_change(self):
        with temp_dir() as tmp:
            base = Path(tmp)
            data = base / "data"
            state = base / "state"
            data.mkdir()
            write_bundle(data, [make_session("S-001", "D-1")], [], [], [])
            store = Store.create(state)

            cycles = {"n": 0}

            def fake_sleep(_interval):
                cycles["n"] += 1
                if cycles["n"] == 1:
                    # 第二轮前追加新会话文件内容
                    write_bundle(
                        data,
                        [make_session("S-001", "D-1"),
                         make_session("S-002", "D-2")],
                        [], [], [],
                    )

            log = watch(store, data, poll_interval=0, max_cycles=3,
                        sleep=fake_sleep)
            self.assertEqual(len(log), 2)
            self.assertEqual(log[1]["summary"]["sessions"]["new"], 1)
            self.assertEqual(log[1]["summary"]["sessions"]["skipped"], 1)
            self.assertEqual(len(store.sessions()), 2)

    def test_watch_stops_when_directory_vanishes(self):
        with temp_dir() as tmp:
            state = Path(tmp) / "state"
            store = Store.create(state)
            missing = Path(tmp) / "gone"
            log = watch(store, missing, poll_interval=0, max_cycles=3)
            self.assertEqual(log, [])


if __name__ == "__main__":
    unittest.main()
