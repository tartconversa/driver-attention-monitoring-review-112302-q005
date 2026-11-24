"""分层指标与证据分级规则测试。"""

from __future__ import annotations

import unittest

from _helpers import make_annotation, make_feedback, make_session

from driver_attention_monitoring_review.metrics import (
    ConditionMetrics,
    EvidenceRules,
    classify,
    compute_all,
    compute_condition_metrics,
    diff_metrics,
)
from driver_attention_monitoring_review.taxonomy import get_taxonomy


class MetricsTests(unittest.TestCase):
    def test_prompt_rate_is_system_rate_not_inattention(self):
        # 60 分钟内 2 次提示 => 2.0 次/小时；同驾驶员两段会话
        sessions = [
            make_session("S-1", "D-1", minutes=30, prompts=1),
            make_session("S-2", "D-1", minutes=30, prompts=1),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics(
            "C1-none-day", tax, sessions, [], []
        )
        self.assertEqual(m.sessions_total, 2)
        self.assertEqual(m.distinct_drivers, 1)
        self.assertAlmostEqual(m.prompt_rate_per_hour, 2.0)
        self.assertAlmostEqual(m.driver_level_prompt_rate_mean, 2.0)
        self.assertTrue(
            any("不等同于驾驶员注意力缺失率" in c for c in m.caveats)
        )

    def test_unobservable_ratio_does_not_count_as_not_watching(self):
        sessions = [
            make_session("S-1", "D-1", camera="occluded"),
            make_session("S-2", "D-2", camera="unknown"),
            make_session("S-3", "D-3", camera="available"),
            make_session("S-4", "D-4", camera="available"),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics("C1-none-day", tax, sessions, [], [])
        self.assertEqual(m.unobservable_sessions, 2)
        self.assertEqual(m.unobservable_ratio, 0.5)
        self.assertEqual(m.observable_sessions, 2)
        self.assertTrue(
            any("不能直接判定驾驶员未注视道路" in c for c in m.caveats)
        )

    def test_observable_only_rate_separates_camera_available_sessions(self):
        sessions = [
            make_session("S-1", "D-1", minutes=30, camera="available", prompts=1),
            make_session("S-2", "D-2", minutes=30, camera="occluded", prompts=4),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics("C1-none-day", tax, sessions, [], [])
        # 仅可用摄像头：1 次 / 0.5 小时 = 2 次/小时；整体为 5 次/小时
        self.assertEqual(m.prompt_rate_observable_per_hour, 2.0)
        self.assertEqual(m.prompt_rate_per_hour, 5.0)

    def test_feedback_coverage_and_respondent_only_rating(self):
        sessions = [make_session(f"S-{i}", f"D-{i}") for i in range(1, 11)]
        feedback = [
            make_feedback("F-1", "S-1", "D-1", rating=2),
            make_feedback("F-2", "S-2", "D-2", rating=4),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics("C1-none-day", tax, sessions, feedback, [])
        self.assertEqual(m.feedback_sessions, 2)
        self.assertEqual(m.feedback_coverage, 0.2)
        self.assertEqual(m.rating_mean_respondents_only, 3.0)
        self.assertTrue(
            any("仅代表少数回复者" in c for c in m.caveats)
        )

    def test_no_feedback_means_none_not_zero_rating(self):
        sessions = [make_session("S-1", "D-1")]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics("C1-none-day", tax, sessions, [], [])
        self.assertIsNone(m.rating_mean_respondents_only)
        self.assertEqual(m.feedback_coverage, 0.0)

    def test_double_annotation_agreement_and_disagreement(self):
        sessions = [make_session(f"S-{i}", f"D-{i}") for i in range(1, 5)]
        annotations = [
            make_annotation("A-1", "S-1", reviewer="R1", label="attentive"),
            make_annotation("A-2", "S-1", reviewer="R2", label="attentive"),
            make_annotation("A-3", "S-2", reviewer="R1", label="attentive"),
            make_annotation("A-4", "S-2", reviewer="R2", label="away"),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics(
            "C1-none-day", tax, sessions, [], annotations
        )
        self.assertEqual(m.double_annotated_sessions, 2)
        self.assertEqual(m.annotation_pair_agreement, 0.5)

    def test_agreement_unevaluable_without_double_annotations(self):
        sessions = [make_session("S-1", "D-1")]
        annotations = [make_annotation("A-1", "S-1")]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics(
            "C1-none-day", tax, sessions, [], annotations
        )
        self.assertIsNone(m.annotation_pair_agreement)
        self.assertTrue(
            any("无法评估" in c for c in m.caveats)
        )

    def test_version_breakdown_splits_v14_v15(self):
        sessions = [
            make_session("S-1", "D-1", version="V14", prompts=2),
            make_session("S-2", "D-2", version="V15", prompts=0),
        ]
        tax = get_taxonomy("v1")
        m = compute_condition_metrics("C1-none-day", tax, sessions, [], [])
        self.assertEqual(set(m.version_breakdown), {"V14", "V15"})
        self.assertGreater(
            m.version_breakdown["V14"]["prompt_rate_per_hour"],
            m.version_breakdown["V15"]["prompt_rate_per_hour"],
        )


class ClassificationTests(unittest.TestCase):
    def _rich_metrics(self, **overrides) -> ConditionMetrics:
        defaults = dict(
            condition_id="C1-none-day",
            taxonomy_version="v1",
            sessions_total=12,
            distinct_drivers=6,
            minutes_total=240,
            prompts_total=4,
            prompt_rate_per_hour=1.0,
            observable_sessions=10,
            unobservable_sessions=2,
            unobservable_ratio=1 / 6,
            camera_breakdown={"available": 10, "occluded": 1, "unknown": 1},
            sessions_with_prompt_ratio=0.25,
            prompt_rate_observable_per_hour=0.8,
            feedback_sessions=6,
            feedback_coverage=0.5,
            feedback_driver_coverage=0.5,
            rating_mean_respondents_only=4.0,
            consent_scope_breakdown={"retention-ok": 3, "research-only": 3},
            reviewed_sessions=4,
            review_coverage=1 / 3,
            reviewers=3,
            label_distribution={"attentive": 3, "away": 1, "unclear": 0},
            unclear_label_ratio=0.0,
            double_annotated_sessions=3,
            annotation_pair_agreement=1.0,
        )
        defaults.update(overrides)
        return ConditionMetrics(**defaults)

    def test_sufficient_when_all_thresholds_met(self):
        classification, reasons, action = classify(
            self._rich_metrics(), EvidenceRules()
        )
        self.assertEqual(classification, "sufficient")
        self.assertEqual(action, "continue_monitoring")

    def test_low_volume_routes_to_research(self):
        m = self._rich_metrics(sessions_total=3, distinct_drivers=2)
        classification, _, action = classify(m, EvidenceRules())
        self.assertEqual(classification, "needs_research")
        self.assertEqual(action, "request_research")

    def test_missing_agreement_routes_to_review_not_treated_as_consistent(self):
        m = self._rich_metrics(annotation_pair_agreement=None)
        classification, reasons, _ = classify(m, EvidenceRules())
        self.assertEqual(classification, "needs_review")
        self.assertTrue(any("无法评估" in r for r in reasons))

    def test_high_unobservable_routes_to_review(self):
        m = self._rich_metrics(unobservable_ratio=0.8, observable_sessions=2,
                               unobservable_sessions=10)
        classification, _, _ = classify(m, EvidenceRules())
        self.assertEqual(classification, "needs_review")

    def test_empty_condition_is_unknown(self):
        m = self._rich_metrics(
            sessions_total=0, distinct_drivers=0, minutes_total=0,
            prompts_total=0, prompt_rate_per_hour=0.0,
            observable_sessions=0, unobservable_sessions=0,
            unobservable_ratio=0.0,
            camera_breakdown={"available": 0, "occluded": 0, "unknown": 0},
            sessions_with_prompt_ratio=0.0,
            prompt_rate_observable_per_hour=None,
            feedback_sessions=0, feedback_coverage=0.0,
            feedback_driver_coverage=0.0,
            rating_mean_respondents_only=None,
            consent_scope_breakdown={"retention-ok": 0, "research-only": 0},
            reviewed_sessions=0, review_coverage=0.0, reviewers=0,
            label_distribution={"attentive": 0, "away": 0, "unclear": 0},
            unclear_label_ratio=0.0, double_annotated_sessions=0,
            annotation_pair_agreement=None,
        )
        classification, _, action = classify(m, EvidenceRules())
        self.assertEqual(classification, "unknown")
        self.assertEqual(action, "request_research")

    def test_compute_all_covers_every_condition(self):
        sessions = [make_session("S-1", "D-1")]
        results = compute_all(get_taxonomy("v1"), sessions, [], [])
        self.assertEqual(len(results), 12)
        empty = [r for r in results.values()
                 if r["metrics"].sessions_total == 0]
        self.assertEqual(len(empty), 11)

    def test_diff_metrics_detects_changes(self):
        before = self._rich_metrics()
        after = self._rich_metrics(prompt_rate_per_hour=2.5,
                                   sessions_total=14)
        changes = diff_metrics(before, after)
        self.assertEqual(changes["prompt_rate_per_hour"],
                         {"old": 1.0, "new": 2.5})
        self.assertEqual(changes["sessions_total"], {"old": 12, "new": 14})


if __name__ == "__main__":
    unittest.main()
