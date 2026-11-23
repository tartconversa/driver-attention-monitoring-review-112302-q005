from __future__ import annotations

import csv
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MaterialTests(unittest.TestCase):
    def test_files_have_declared_headers_and_rows(self):
        expected = EXPECTED
        for filename, (columns, minimum_rows) in expected.items():
            with self.subTest(filename=filename):
                with (ROOT / "data" / filename).open(encoding="utf-8-sig", newline="") as stream:
                    reader = csv.DictReader(stream)
                    rows = list(reader)
                self.assertEqual(reader.fieldnames, columns)
                self.assertGreaterEqual(len(rows), minimum_rows)
                self.assertTrue(all(None not in row for row in rows))


EXPECTED = {'sessions.csv': (['session_id', 'driver_key', 'version', 'wear', 'lighting', 'task_minutes', 'camera_state', 'prompt_count', 'region'], 60), 'feedback.csv': (['response_id', 'driver_key', 'session_id', 'survey_wave', 'submitted_at', 'rating', 'consent_scope'], 20), 'annotations.csv': (['annotation_id', 'session_id', 'reviewer', 'attention_label', 'visibility', 'review_batch'], 24), 'research_slots.csv': (['slot_id', 'region', 'device', 'wear_condition', 'date', 'capacity', 'consent_state', 'status'], 21)}


if __name__ == "__main__":
    unittest.main()
