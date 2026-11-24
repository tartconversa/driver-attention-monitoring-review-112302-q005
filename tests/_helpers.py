"""测试公共工具：把 src 加入路径，提供小型内存资料构造器。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from driver_attention_monitoring_review.models import (  # noqa: E402
    Annotation,
    FeedbackResponse,
    ResearchSlot,
    Session,
)


def make_session(
    session_id: str,
    driver_key: str | None = None,
    *,
    version: str = "V15",
    wear: str = "none",
    lighting: str = "day",
    minutes: int = 20,
    camera: str = "available",
    prompts: int = 0,
    region: str = "R-S",
) -> Session:
    return Session(
        session_id=session_id,
        driver_key=driver_key or f"D-{session_id}",
        version=version,
        wear=wear,
        lighting=lighting,
        task_minutes=minutes,
        camera_state=camera,
        prompt_count=prompts,
        region=region,
    )


def make_feedback(
    response_id: str,
    session_id: str,
    driver_key: str,
    *,
    rating: int = 3,
    scope: str = "retention-ok",
) -> FeedbackResponse:
    return FeedbackResponse(
        response_id=response_id,
        driver_key=driver_key,
        session_id=session_id,
        survey_wave="SW-1",
        submitted_at="2026-11-10T10:00:00Z",
        rating=rating,
        consent_scope=scope,
    )


def make_annotation(
    annotation_id: str,
    session_id: str,
    *,
    reviewer: str = "REV-01",
    label: str = "attentive",
    visibility: str = "visible",
    batch: str = "RB-0",
) -> Annotation:
    return Annotation(
        annotation_id=annotation_id,
        session_id=session_id,
        reviewer=reviewer,
        attention_label=label,
        visibility=visibility,
        review_batch=batch,
    )


def make_slot(
    slot_id: str,
    *,
    wear: str = "hat",
    capacity: int = 3,
    consent: str = "active",
    status: str = "open",
) -> ResearchSlot:
    return ResearchSlot(
        slot_id=slot_id,
        region="R-S",
        device="CAM-A",
        wear_condition=wear,
        date="2026-12-15",
        capacity=capacity,
        consent_state=consent,
        status=status,
    )


def temp_dir() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory()
