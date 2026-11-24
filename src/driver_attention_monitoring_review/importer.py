"""CSV 资料导入与契约校验。

设计要点：
- 只读外部 CSV，不修改输入文件；
- 全量校验后再返回，任何一行违反词表/类型/引用约束即拒绝整批导入；
- 以内容哈希识别记录，配合 store 实现幂等的持续导入与目录轮询。
"""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path

from .models import (
    Annotation,
    CONSENT_SCOPES,
    CONSENT_STATES,
    FeedbackResponse,
    LIGHTING_VALUES,
    ResearchSlot,
    Session,
    SLOT_BASELINE_STATUS,
    ValidationError,
    VERSION_VALUES,
    VISIBILITY_VALUES,
    ATTENTION_LABELS,
    WEAR_VALUES,
    CAMERA_VALUES,
)

SESSION_FILE = "sessions.csv"
FEEDBACK_FILE = "feedback.csv"
ANNOTATION_FILE = "annotations.csv"
SLOT_FILE = "research_slots.csv"


def content_hash(row: dict[str, str]) -> str:
    """单行内容的稳定哈希，用于幂等识别（不含行号、不含导入时间）。"""
    payload = "\x1f".join(f"{k}={row[k]}" for k in sorted(row))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise ValidationError(f"输入文件不存在：{path}")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise ValidationError(f"文件缺少表头：{path}")
        rows = list(reader)
    return list(reader.fieldnames), rows


def _require_headers(path: Path, actual: list[str], expected: list[str]) -> None:
    if actual != expected:
        raise ValidationError(
            f"{path.name} 表头不匹配：期望 {expected}，实际 {actual}"
        )


def _as_int(row: dict[str, str], key: str, row_ref: str) -> int:
    try:
        return int(row[key])
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{row_ref} 的 {key} 不是整数：{row.get(key)!r}") from exc


def _check_choice(
    row: dict[str, str], key: str, allowed: tuple[str, ...], row_ref: str
) -> None:
    if row[key] not in allowed:
        raise ValidationError(
            f"{row_ref} 的 {key}={row[key]!r} 不在受控词表 {allowed} 内"
        )


def load_sessions(path: Path) -> list[Session]:
    headers, rows = _read_csv(path)
    expected = [
        "session_id",
        "driver_key",
        "version",
        "wear",
        "lighting",
        "task_minutes",
        "camera_state",
        "prompt_count",
        "region",
    ]
    _require_headers(path, headers, expected)
    sessions: list[Session] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        ref = f"sessions.csv 第{i}行"
        for key in expected:
            if row.get(key) in (None, ""):
                raise ValidationError(f"{ref} 缺少字段 {key}")
        _check_choice(row, "version", VERSION_VALUES, ref)
        _check_choice(row, "wear", WEAR_VALUES, ref)
        _check_choice(row, "lighting", LIGHTING_VALUES, ref)
        _check_choice(row, "camera_state", CAMERA_VALUES, ref)
        minutes = _as_int(row, "task_minutes", ref)
        prompts = _as_int(row, "prompt_count", ref)
        if minutes <= 0:
            raise ValidationError(f"{ref} task_minutes 必须为正数")
        if prompts < 0:
            raise ValidationError(f"{ref} prompt_count 不能为负")
        if row["session_id"] in seen:
            raise ValidationError(f"{ref} session_id={row['session_id']} 重复")
        seen.add(row["session_id"])
        sessions.append(
            Session(
                session_id=row["session_id"],
                driver_key=row["driver_key"],
                version=row["version"],
                wear=row["wear"],
                lighting=row["lighting"],
                task_minutes=minutes,
                camera_state=row["camera_state"],
                prompt_count=prompts,
                region=row["region"],
            )
        )
    return sessions


def load_feedback(path: Path, known_sessions: set[str]) -> list[FeedbackResponse]:
    headers, rows = _read_csv(path)
    expected = [
        "response_id",
        "driver_key",
        "session_id",
        "survey_wave",
        "submitted_at",
        "rating",
        "consent_scope",
    ]
    _require_headers(path, headers, expected)
    out: list[FeedbackResponse] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        ref = f"feedback.csv 第{i}行"
        for key in expected:
            if row.get(key) in (None, ""):
                raise ValidationError(f"{ref} 缺少字段 {key}")
        if row["session_id"] not in known_sessions:
            raise ValidationError(f"{ref} 引用了未知 session_id={row['session_id']}")
        _check_choice(row, "consent_scope", CONSENT_SCOPES, ref)
        rating = _as_int(row, "rating", ref)
        if not 1 <= rating <= 5:
            raise ValidationError(f"{ref} rating 必须在 1..5 之间")
        if row["response_id"] in seen:
            raise ValidationError(f"{ref} response_id={row['response_id']} 重复")
        seen.add(row["response_id"])
        out.append(
            FeedbackResponse(
                response_id=row["response_id"],
                driver_key=row["driver_key"],
                session_id=row["session_id"],
                survey_wave=row["survey_wave"],
                submitted_at=row["submitted_at"],
                rating=rating,
                consent_scope=row["consent_scope"],
            )
        )
    return out


def load_annotations(path: Path, known_sessions: set[str]) -> list[Annotation]:
    headers, rows = _read_csv(path)
    expected = [
        "annotation_id",
        "session_id",
        "reviewer",
        "attention_label",
        "visibility",
        "review_batch",
    ]
    _require_headers(path, headers, expected)
    out: list[Annotation] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        ref = f"annotations.csv 第{i}行"
        for key in expected:
            if row.get(key) in (None, ""):
                raise ValidationError(f"{ref} 缺少字段 {key}")
        if row["session_id"] not in known_sessions:
            raise ValidationError(f"{ref} 引用了未知 session_id={row['session_id']}")
        _check_choice(row, "attention_label", ATTENTION_LABELS, ref)
        _check_choice(row, "visibility", VISIBILITY_VALUES, ref)
        if row["annotation_id"] in seen:
            raise ValidationError(f"{ref} annotation_id={row['annotation_id']} 重复")
        seen.add(row["annotation_id"])
        out.append(
            Annotation(
                annotation_id=row["annotation_id"],
                session_id=row["session_id"],
                reviewer=row["reviewer"],
                attention_label=row["attention_label"],
                visibility=row["visibility"],
                review_batch=row["review_batch"],
            )
        )
    return out


def load_slots(path: Path) -> list[ResearchSlot]:
    headers, rows = _read_csv(path)
    expected = [
        "slot_id",
        "region",
        "device",
        "wear_condition",
        "date",
        "capacity",
        "consent_state",
        "status",
    ]
    _require_headers(path, headers, expected)
    out: list[ResearchSlot] = []
    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):
        ref = f"research_slots.csv 第{i}行"
        for key in expected:
            if row.get(key) in (None, ""):
                raise ValidationError(f"{ref} 缺少字段 {key}")
        _check_choice(row, "consent_state", CONSENT_STATES, ref)
        _check_choice(row, "status", SLOT_BASELINE_STATUS, ref)
        _check_choice(row, "wear_condition", WEAR_VALUES, ref)
        capacity = _as_int(row, "capacity", ref)
        if capacity <= 0:
            raise ValidationError(f"{ref} capacity 必须为正数")
        if row["slot_id"] in seen:
            raise ValidationError(f"{ref} slot_id={row['slot_id']} 重复")
        seen.add(row["slot_id"])
        out.append(
            ResearchSlot(
                slot_id=row["slot_id"],
                region=row["region"],
                device=row["device"],
                wear_condition=row["wear_condition"],
                date=row["date"],
                capacity=capacity,
                consent_state=row["consent_state"],
                status=row["status"],
            )
        )
    return out


def load_bundle(data_dir: str | Path) -> dict[str, list]:
    """加载并交叉校验整批资料。任一行不合规即抛出 ValidationError。"""
    base = Path(data_dir)
    sessions = load_sessions(base / SESSION_FILE)
    known = {s.session_id for s in sessions}
    feedback = load_feedback(base / FEEDBACK_FILE, known)
    annotations = load_annotations(base / ANNOTATION_FILE, known)
    slots = load_slots(base / SLOT_FILE)
    return {
        "sessions": sessions,
        "feedback": feedback,
        "annotations": annotations,
        "slots": slots,
    }
