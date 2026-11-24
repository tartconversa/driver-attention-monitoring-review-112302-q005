"""导入校验与幂等性测试。"""

from __future__ import annotations

import csv
import unittest
from pathlib import Path

from _helpers import make_annotation, make_feedback, make_session, make_slot, temp_dir

from driver_attention_monitoring_review.importer import (
    content_hash,
    load_bundle,
)
from driver_attention_monitoring_review.models import Session, ValidationError
from driver_attention_monitoring_review.store import Store

HEADERS = {
    "sessions.csv": [
        "session_id", "driver_key", "version", "wear", "lighting",
        "task_minutes", "camera_state", "prompt_count", "region",
    ],
    "feedback.csv": [
        "response_id", "driver_key", "session_id", "survey_wave",
        "submitted_at", "rating", "consent_scope",
    ],
    "annotations.csv": [
        "annotation_id", "session_id", "reviewer", "attention_label",
        "visibility", "review_batch",
    ],
    "research_slots.csv": [
        "slot_id", "region", "device", "wear_condition", "date",
        "capacity", "consent_state", "status",
    ],
}


def write_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def write_bundle(directory: Path, sessions, feedback, annotations, slots) -> None:
    write_csv(directory / "sessions.csv", HEADERS["sessions.csv"],
              [s.to_dict() for s in sessions])
    write_csv(directory / "feedback.csv", HEADERS["feedback.csv"],
              [f.to_dict() for f in feedback])
    write_csv(directory / "annotations.csv", HEADERS["annotations.csv"],
              [a.to_dict() for a in annotations])
    write_csv(directory / "research_slots.csv", HEADERS["research_slots.csv"],
              [s.to_dict() for s in slots])


class ImporterTests(unittest.TestCase):
    def _baseline(self):
        sessions = [make_session(f"S-{i:03d}", f"D-{(i % 3) + 1:02d}")
                    for i in range(1, 6)]
        feedback = [make_feedback("F-001", "S-001", "D-001")]
        annotations = [make_annotation("A-001", "S-001")]
        slots = [make_slot("RS-01")]
        return sessions, feedback, annotations, slots

    def test_loads_well_formed_bundle(self):
        with temp_dir() as tmp:
            base = Path(tmp) / "data"
            base.mkdir()
            write_bundle(base, *self._baseline())
            bundle = load_bundle(base)
            self.assertEqual(len(bundle["sessions"]), 5)
            self.assertEqual(bundle["feedback"][0].rating, 3)

    def test_rejects_unknown_vocabulary(self):
        with temp_dir() as tmp:
            base = Path(tmp) / "data"
            base.mkdir()
            sessions, feedback, annotations, slots = self._baseline()
            sessions[0] = make_session("S-001", "D-001", wear="helmet")
            write_bundle(base, sessions, feedback, annotations, slots)
            with self.assertRaises(ValidationError):
                load_bundle(base)

    def test_rejects_dangling_feedback_reference(self):
        with temp_dir() as tmp:
            base = Path(tmp) / "data"
            base.mkdir()
            sessions, _, annotations, slots = self._baseline()
            bad_feedback = [make_feedback("F-999", "S-404", "D-001")]
            write_bundle(base, sessions, bad_feedback, annotations, slots)
            with self.assertRaises(ValidationError):
                load_bundle(base)

    def test_rejects_negative_prompt_count(self):
        with temp_dir() as tmp:
            base = Path(tmp) / "data"
            base.mkdir()
            sessions, feedback, annotations, slots = self._baseline()
            sessions[0] = Session(**{**sessions[0].to_dict(), "prompt_count": -1})
            write_bundle(base, sessions, feedback, annotations, slots)
            with self.assertRaises(ValidationError):
                load_bundle(base)

    def test_content_hash_stable_and_distinct(self):
        row1 = {"a": "1", "b": "x"}
        row2 = {"b": "x", "a": "1"}
        row3 = {"a": "2", "b": "x"}
        self.assertEqual(content_hash(row1), content_hash(row2))
        self.assertNotEqual(content_hash(row1), content_hash(row3))

    def test_idempotent_import_skips_unchanged_rows(self):
        with temp_dir() as tmp:
            tmp_path = Path(tmp)
            data = tmp_path / "data"
            state = tmp_path / "state"
            data.mkdir()
            bundle_rows = self._baseline()
            write_bundle(data, *bundle_rows)
            store = Store.create(state)
            first = store.import_bundle(load_bundle(data), str(data))
            self.assertEqual(first["sessions"]["new"], 5)
            second = store.import_bundle(load_bundle(data), str(data))
            self.assertEqual(second["sessions"]["new"], 0)
            self.assertEqual(second["sessions"]["skipped"], 5)
            self.assertEqual(len(store.sessions()), 5)

    def test_changed_row_with_same_id_is_rejected(self):
        with temp_dir() as tmp:
            tmp_path = Path(tmp)
            data = tmp_path / "data"
            state = tmp_path / "state"
            data.mkdir()
            sessions, feedback, annotations, slots = self._baseline()
            write_bundle(data, sessions, feedback, annotations, slots)
            store = Store.create(state)
            store.import_bundle(load_bundle(data), str(data))
            sessions[0] = make_session("S-001", "D-001", prompts=9)
            write_bundle(data, sessions, feedback, annotations, slots)
            with self.assertRaises(ValidationError):
                store.import_bundle(load_bundle(data), str(data))


if __name__ == "__main__":
    unittest.main()
