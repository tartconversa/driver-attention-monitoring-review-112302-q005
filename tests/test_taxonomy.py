"""分类法版本切换与结论适用范围重标测试。"""

from __future__ import annotations

import unittest

from _helpers import make_annotation, make_feedback, make_session, temp_dir

from driver_attention_monitoring_review.analysis import run_analysis
from driver_attention_monitoring_review.metrics import EvidenceRules
from driver_attention_monitoring_review.store import Store
from driver_attention_monitoring_review.taxonomy import (
    build_mapping,
    get_taxonomy,
    is_many_to_one,
)
from driver_attention_monitoring_review.watch import import_once
from pathlib import Path


class TaxonomyTests(unittest.TestCase):
    def test_v1_has_twelve_conditions_v2_has_eight(self):
        self.assertEqual(len(get_taxonomy("v1").conditions), 12)
        self.assertEqual(len(get_taxonomy("v2").conditions), 8)

    def test_v2_groups_dusk_and_night_into_low_light(self):
        tax = get_taxonomy("v2")
        self.assertEqual(tax.match("hat", "dusk"), tax.match("hat", "night"))
        self.assertEqual(tax.match("hat", "dusk"), "C2-hat-low_light")
        self.assertEqual(tax.match("none", "day"), "C2-no_wear-day")
        self.assertEqual(tax.match("both", "night"),
                         "C2-hat_plus_sunglasses-low_light")

    def test_mapping_is_many_to_one_for_low_light(self):
        mapping = build_mapping("v1", "v2")
        self.assertEqual(mapping["C1-hat-dusk"], "C2-hat-low_light")
        self.assertEqual(mapping["C1-hat-night"], "C2-hat-low_light")
        merged = is_many_to_one("v1", "v2")
        self.assertTrue(merged["C2-hat-low_light"])
        self.assertFalse(merged["C2-hat-day"])

    def test_activation_rescopes_existing_snapshots(self):
        with temp_dir() as tmp:
            base = Path(tmp)
            data = base / "data"
            state = base / "state"
            data.mkdir()
            sessions = [
                make_session("S-1", "D-1", wear="hat", lighting="dusk"),
                make_session("S-2", "D-2", wear="hat", lighting="night"),
            ]
            from tests_support import write_bundle
            write_bundle(data, sessions, [], [], [])
            store = Store.create(state)
            import_once(store, data)
            run_analysis(store, rules=EvidenceRules(min_sessions=1,
                                                    min_distinct_drivers=1,
                                                    min_feedback_coverage=0.0,
                                                    min_reviewed_sessions=0,
                                                    min_agreement_pairs=99))
            v1_snaps = [s for s in store.state["snapshots"]
                        if s["condition_id"] == "C1-hat-dusk"]
            self.assertTrue(v1_snaps)

            result = store.activate_taxonomy("v2", "lead", "合并低光条件")
            # 旧快照被标 superseded，并指向新条件
            self.assertTrue(
                all(s["applicability"] == "superseded"
                    for s in store.state["snapshots"]
                    if s["taxonomy_version"] == "v1")
            )
            carried = [s for s in store.state["snapshots"]
                       if s["applicability"] == "carried_over"]
            target_ids = {s["condition_id"] for s in carried}
            self.assertIn("C2-hat-low_light", target_ids)
            self.assertTrue(any("待复核确认" in r for s in carried
                                for r in s["reasons"]))
            self.assertEqual(store.state["active_taxonomy"], "v2")
            self.assertTrue(result["remarks"])

    def test_activation_is_idempotent_guard(self):
        with temp_dir() as tmp:
            store = Store.create(Path(tmp) / "state")
            from driver_attention_monitoring_review.models import BusinessRuleError
            with self.assertRaises(BusinessRuleError):
                store.activate_taxonomy("v1", "lead", "重复启用")


if __name__ == "__main__":
    unittest.main()
