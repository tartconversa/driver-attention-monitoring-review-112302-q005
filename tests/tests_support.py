"""测试用 CSV 落盘工具（供多个测试模块共享）。"""

from __future__ import annotations

import csv
from pathlib import Path

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


def write_bundle(directory: Path, sessions, feedback, annotations, slots) -> None:
    payloads = {
        "sessions.csv": [x.to_dict() for x in sessions],
        "feedback.csv": [x.to_dict() for x in feedback],
        "annotations.csv": [x.to_dict() for x in annotations],
        "research_slots.csv": [x.to_dict() for x in slots],
    }
    for filename, rows in payloads.items():
        with (directory / filename).open("w", encoding="utf-8-sig",
                                         newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=HEADERS[filename])
            writer.writeheader()
            writer.writerows(rows)
