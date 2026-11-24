"""端到端工作流：导入 → 分析 → 决策 → 新数据改变结论 → 追溯。"""

from __future__ import annotations

import unittest
from pathlib import Path

from _helpers import (
    make_annotation,
    make_feedback,
    make_session,
    make_slot,
    temp_dir,
)
from tests_support import write_bundle

from driver_attention_monitoring_review.analysis import run_analysis
from driver_attention_monitoring_review.metrics import EvidenceRules
from driver_attention_monitoring_review.models import Decision
from driver_attention_monitoring_review.report import full_report
from driver_attention_monitoring_review.store import Store
from driver_attention_monitoring_review.synthdata import generate
from driver_attention_monitoring_review.taxonomy import get_taxonomy, now_utc
from driver_attention_monitoring_review.watch import import_once


RULES = EvidenceRules(
    min_sessions=4,
    min_distinct_drivers=3,
    max_unobservable_ratio=0.5,
    min_feedback_coverage=0.4,
    min_reviewed_sessions=2,
    min_agreement_pairs=1,
    min_agreement=0.8,
)


class EndToEndTests(unittest.TestCase):
    def test_full_loop_with_decision_flagging_and_trace(self):
        with temp_dir() as tmp:
            base = Path(tmp)
            data = base / "data"
            state = base / "state"
            data.mkdir()

            # 初始：hat/day 条件样本偏少、无反馈 -> needs_research
            sessions = [
                make_session("S-1", "D-1", wear="hat", lighting="day", prompts=1),
                make_session("S-2", "D-2", wear="hat", lighting="day", prompts=0),
            ]
            write_bundle(data, sessions, [], [], [make_slot("RS-01")])
            store = Store.create(state)
            import_once(store, data)

            first = run_analysis(store, rules=RULES)
            hat_day = first["results"]["C1-hat-day"]
            self.assertEqual(hat_day["classification"], "needs_research")
            snap_id = first["snapshots"][0].snapshot_id
            # 快照 ID 顺序与条件顺序一致，精确定位 hat-day 快照
            snap_id = next(
                s.snapshot_id for s in first["snapshots"]
                if s.condition_id == "C1-hat-day"
            )

            # 负责人据此申请研究
            decision = Decision(
                decision_id=store._next_id("DEC-"),
                ts=now_utc(), actor="体验负责人",
                condition_id="C1-hat-day",
                taxonomy_version="v1",
                action="request_research",
                snapshot_id=snap_id,
                note="帽檐日间反馈覆盖不足，申请补充研究",
            )
            store.add_decision(decision)
            plan = store.create_plan(
                "研究协调员", ["C1-hat-day"], "R-S", "针对性补充"
            )
            store.accept_plan(plan.plan_id, ["RS-01"], "体验负责人")

            # 第二批资料：样本与反馈补齐、加双注（一致）-> 升级 sufficient
            extra = [
                make_session("S-3", "D-3", wear="hat", lighting="day", prompts=0),
                make_session("S-4", "D-4", wear="hat", lighting="day", prompts=1),
                make_session("S-5", "D-1", wear="hat", lighting="day", prompts=0),
            ]
            feedback = [
                make_feedback("F-1", "S-1", "D-1", rating=4),
                make_feedback("F-2", "S-2", "D-2", rating=5),
            ]
            annotations = [
                make_annotation("A-1", "S-1", reviewer="R1", label="attentive"),
                make_annotation("A-2", "S-1", reviewer="R2", label="attentive"),
                make_annotation("A-3", "S-2", reviewer="R1", label="away"),
            ]
            write_bundle(data, sessions + extra, feedback, annotations,
                         [make_slot("RS-01")])
            summary = import_once(store, data)
            self.assertEqual(summary["sessions"]["new"], 3)

            second = run_analysis(store, rules=RULES)
            self.assertEqual(
                second["results"]["C1-hat-day"]["classification"],
                "sufficient",
            )
            # 原决策被标记为受新结果影响
            self.assertIn("C1-hat-day", second["changed_conditions"])
            self.assertIn(decision.decision_id, second["affected_decisions"])
            refreshed = next(
                d for d in store.state["decisions"]
                if d["decision_id"] == decision.decision_id
            )
            self.assertTrue(refreshed["affected_by_change"])

            # 追溯能回答「为什么曾未知、谁接受了计划、什么改变了决策」
            trace = store.condition_trace("C1-hat-day")
            self.assertEqual(len(trace["snapshots"]), 2)
            self.assertEqual(trace["plans"][0]["accepted_by"], "体验负责人")
            self.assertEqual(trace["decisions"][0]["affected_by_change"], True)

            # 报告包含护栏声明
            text = full_report(get_taxonomy("v1"), second["results"])
            self.assertIn("提示次数不是注意力缺失次数", text)
            self.assertIn("不得外推至未回复用户", text)

    def test_synthetic_generator_produces_consistent_bundle(self):
        with temp_dir() as tmp:
            out = Path(tmp) / "synth"
            counts = generate(out, seed=42)
            self.assertEqual(counts["sessions.csv"], 72)
            # 固定种子可复现
            out2 = Path(tmp) / "synth2"
            counts2 = generate(out2, seed=42)
            self.assertEqual(counts, counts2)
            for name in counts:
                self.assertEqual(
                    (out / name).read_bytes(), (out2 / name).read_bytes()
                )
            # 生成资料能通过应用自身的契约校验与完整分析
            store = Store.create(Path(tmp) / "state")
            import_once(store, out)
            outcome = run_analysis(store)
            self.assertEqual(len(outcome["results"]), 12)
            classifications = {
                r["classification"] for r in outcome["results"].values()
            }
            # 合成数据刻意制造了分层差异，不应全部落在同一档
            self.assertGreater(len(classifications), 1)


if __name__ == "__main__":
    unittest.main()
