"""领域模型与取值约束。

所有取值均为合成资料中的受控词表；缺失、待复核、撤回是有效业务事实，
不能由应用自行解释成零事件或成功状态。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

# ---- 受控词表（与 docs/资料契约.md 保持一致） ----

WEAR_VALUES = ("none", "hat", "sunglasses", "both")
LIGHTING_VALUES = ("day", "dusk", "night")
CAMERA_VALUES = ("available", "occluded", "unknown")
VERSION_VALUES = ("V14", "V15")

ATTENTION_LABELS = ("attentive", "away", "unclear")
VISIBILITY_VALUES = ("visible", "partial", "blocked")

CONSENT_STATES = ("active", "pending", "withdrawn")
SLOT_BASELINE_STATUS = ("open", "accepted", "done")
CONSENT_SCOPES = ("retention-ok", "research-only")

# 摄像头不可观测的取值。注意：不可观测 != 驾驶员没有注视道路。
UNOBSERVABLE_CAMERA = ("occluded", "unknown")

# 研究名额在运营事件之后派生出来的生命周期状态
SLOT_LIFECYCLE = (
    "open",            # 未接受
    "accepted",        # 已接受、占用名额、尚未执行
    "done",            # 已执行，资料已采集
    "withdrawn",       # 同意撤回，未执行部分已释放
    "device_failed",   # 设备失效，未执行部分已释放
)

RESEARCH_PLAN_STATUS = ("proposed", "accepted", "completed", "cancelled")

MATERIAL_RETENTION = ("retained", "removal_pending", "removed")


class ValidationError(ValueError):
    """输入资料未通过契约校验。"""


class BusinessRuleError(RuntimeError):
    """操作违反业务规则（例如撤回已执行名额、重复占用）。"""


@dataclass(frozen=True)
class Session:
    session_id: str
    driver_key: str
    version: str
    wear: str
    lighting: str
    task_minutes: int
    camera_state: str
    prompt_count: int
    region: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeedbackResponse:
    response_id: str
    driver_key: str
    session_id: str
    survey_wave: str
    submitted_at: str
    rating: int
    consent_scope: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    session_id: str
    reviewer: str
    attention_label: str
    visibility: str
    review_batch: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResearchSlot:
    slot_id: str
    region: str
    device: str
    wear_condition: str
    date: str
    capacity: int
    consent_state: str
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceSnapshot:
    """某条件在某分类法版本下、某时点的证据快照。"""

    snapshot_id: str
    ts: str
    taxonomy_version: str
    condition_id: str
    classification: str          # sufficient / needs_review / needs_research / unknown
    reasons: list[str]
    metrics: dict[str, Any]
    data_hash: str
    applicability: str = "current"       # current / superseded / carried_over
    superseded_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Decision:
    """体验负责人的决策记录。"""

    decision_id: str
    ts: str
    actor: str
    condition_id: str
    taxonomy_version: str
    action: str                  # continue_monitoring / adjust_copy / request_research
    snapshot_id: str
    note: str
    affected_by_change: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchPlan:
    plan_id: str
    ts: str
    created_by: str
    target_conditions: list[str]
    taxonomy_version: str
    region: str | None
    note: str
    status: str = "proposed"
    accepted_by: str | None = None
    accepted_at: str | None = None
    slot_ids: list[str] = field(default_factory=list)
    released_capacity: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CollectedMaterial:
    """研究执行后采集的资料及其授权处置状态。"""

    material_id: str
    slot_id: str
    plan_id: str
    collected_at: str
    consent_scope: str
    retention_state: str = "retained"   # research-only 遇撤回转为 removal_pending/removed
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
