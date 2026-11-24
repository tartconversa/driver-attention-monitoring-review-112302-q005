"""合成数据生成器（固定随机种子）。

生成的数据刻意包含：
- 部分条件样本量不足、部分条件摄像头不可观测比例偏高；
- 仅 1/3 会话有自愿反馈，模拟选择性反馈；
- 少量同片段多标注（含一次分歧），用于复核一致性计算；
- 研究名额覆盖 open/accepted/done 与 active/pending/withdrawn 组合。

所有标识均为虚构，输出为 UTF-8 BOM 编码的 CSV，与基线 data/ 格式一致。
"""

from __future__ import annotations

import csv
import random
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


def generate(output_dir: str | Path, seed: int = 20261124) -> dict[str, int]:
    rng = random.Random(seed)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- 会话：14 名驾驶员，共 72 段，条件冷热不均 ----
    sessions: list[dict[str, str]] = []
    drivers = [f"D-{i:02d}" for i in range(1, 15)]
    # 让 none/hat 在 day/dusk 条件上样本充足，both/night 上刻意稀缺
    wear_plan = (
        ["none"] * 20 + ["hat"] * 20 + ["sunglasses"] * 18 + ["both"] * 14
    )
    lighting_plan = ["day"] * 26 + ["dusk"] * 26 + ["night"] * 20
    rng.shuffle(wear_plan)
    rng.shuffle(lighting_plan)
    sid = 1
    for wear, lighting in zip(wear_plan, lighting_plan):
        driver = drivers[rng.randrange(len(drivers))]
        version = "V15" if sid % 2 == 0 else "V14"
        # both + night 场景摄像头更容易不可观测，模拟 V15 关注的困难条件
        if wear in ("both", "sunglasses") and lighting == "night":
            camera = rng.choices(
                ["available", "occluded", "unknown"], weights=[2, 5, 3]
            )[0]
        elif lighting == "night":
            camera = rng.choices(
                ["available", "occluded", "unknown"], weights=[5, 3, 2]
            )[0]
        else:
            camera = rng.choices(
                ["available", "occluded", "unknown"], weights=[8, 1, 1]
            )[0]
        base_prompts = rng.randint(0, 3)
        if camera == "available":
            prompts = base_prompts
        else:
            # 不可观测时提示可能更频繁，但这只是系统行为，不代表注意力缺失
            prompts = base_prompts + rng.randint(0, 2)
        sessions.append({
            "session_id": f"S-{sid:03d}",
            "driver_key": driver,
            "version": version,
            "wear": wear,
            "lighting": lighting,
            "task_minutes": str(rng.randint(12, 30)),
            "camera_state": camera,
            "prompt_count": str(prompts),
            "region": rng.choice(["R-S", "R-N"]),
        })
        sid += 1

    # ---- 反馈：仅约三分之一会话，自愿且有选择性 ----
    feedback: list[dict[str, str]] = []
    chosen = rng.sample(sessions, k=24)
    for i, sess in enumerate(chosen, start=1):
        feedback.append({
            "response_id": f"F-{i:03d}",
            "driver_key": sess["driver_key"],
            "session_id": sess["session_id"],
            "survey_wave": f"SW-{rng.randint(0, 3)}",
            "submitted_at": f"2026-11-{rng.randint(1, 20):02d}T10:00:00Z",
            "rating": str(rng.randint(1, 5)),
            "consent_scope": rng.choice(["retention-ok", "research-only"]),
        })

    # ---- 标注：28 条，覆盖部分会话；含 4 个双注片段（其中 1 个分歧）----
    annotations: list[dict[str, str]] = []
    reviewable = rng.sample(sessions, k=24)
    aid = 1
    reviewers = [f"REV-{i:02d}" for i in range(6)]
    labels = ["attentive", "away", "unclear"]
    visibilities = ["visible", "partial", "blocked"]
    double_indexes = set(rng.sample(range(len(reviewable)), k=4))
    disagreement_index = next(iter(double_indexes))
    for idx, sess in enumerate(reviewable):
        ann = {
            "annotation_id": f"A-{aid:03d}",
            "session_id": sess["session_id"],
            "reviewer": rng.choice(reviewers),
            "attention_label": rng.choice(labels),
            "visibility": rng.choice(visibilities),
            "review_batch": f"RB-{rng.randint(0, 2)}",
        }
        annotations.append(ann)
        aid += 1
        if idx in double_indexes:
            first = ann["attention_label"]
            if idx == disagreement_index:
                second = next(l for l in labels if l != first)
            else:
                second = first
            annotations.append({
                "annotation_id": f"A-{aid:03d}",
                "session_id": sess["session_id"],
                "reviewer": rng.choice([r for r in reviewers if r != ann["reviewer"]]),
                "attention_label": second,
                "visibility": rng.choice(visibilities),
                "review_batch": ann["review_batch"],
            })
            aid += 1

    # ---- 研究名额：21 个，状态/同意组合齐全 ----
    slots: list[dict[str, str]] = []
    combos = [
        ("open", "active"), ("accepted", "active"), ("done", "active"),
        ("open", "pending"), ("accepted", "pending"), ("done", "pending"),
        ("open", "withdrawn"),
    ]
    slot_wears = ["hat", "sunglasses", "both"]
    for i in range(21):
        status, consent = combos[i % len(combos)]
        slots.append({
            "slot_id": f"RS-{i + 1:02d}",
            "region": rng.choice(["R-S", "R-N"]),
            "device": rng.choice(["CAM-A", "CAM-B"]),
            "wear_condition": rng.choice(slot_wears),
            "date": f"2026-12-{rng.randint(1, 28):02d}",
            "capacity": str(rng.randint(2, 5)),
            "consent_state": consent,
            "status": status,
        })

    tables = {
        "sessions.csv": (HEADERS["sessions.csv"], sessions),
        "feedback.csv": (HEADERS["feedback.csv"], feedback),
        "annotations.csv": (HEADERS["annotations.csv"], annotations),
        "research_slots.csv": (HEADERS["research_slots.csv"], slots),
    }
    counts: dict[str, int] = {}
    for filename, (headers, rows) in tables.items():
        with (out / filename).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        counts[filename] = len(rows)
    return counts
