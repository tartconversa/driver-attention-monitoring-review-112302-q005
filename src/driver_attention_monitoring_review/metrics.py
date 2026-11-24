"""分层指标计算与证据分级规则。

口径护栏（报告中必须保留这些说明，不可只给数字）：
1. 提示率是「系统提示」的发生率，不等于驾驶员注意力缺失率；
2. 摄像头不可观测只表示系统没读到眼部信号，不能直接视为驾驶员未注视道路；
3. 反馈来自自愿调查，覆盖不足时只能描述回复者，不能外推到未回复用户；
4. 同一驾驶员可贡献多段会话，指标同时给出会话口径与驾驶员口径；
5. 没有双人复核样本时，一致性只能标注「无法评估」，不能报告为一致。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from .models import (
    Annotation,
    FeedbackResponse,
    Session,
    UNOBSERVABLE_CAMERA,
)
from .taxonomy import Taxonomy


@dataclass
class EvidenceRules:
    """证据分级阈值，可由调用方调整；默认值供本地合成资料演示。"""

    min_sessions: int = 8
    min_distinct_drivers: int = 4
    max_unobservable_ratio: float = 0.4
    min_feedback_coverage: float = 0.3
    min_reviewed_sessions: int = 3
    min_agreement_pairs: int = 2          # 至少多少个双注会话才评估一致性
    min_agreement: float = 0.8
    max_unclear_label_ratio: float = 0.25


@dataclass
class ConditionMetrics:
    condition_id: str
    taxonomy_version: str
    sessions_total: int
    distinct_drivers: int
    minutes_total: int
    prompts_total: int
    prompt_rate_per_hour: float
    observable_sessions: int
    unobservable_sessions: int
    unobservable_ratio: float
    camera_breakdown: dict[str, int]
    sessions_with_prompt_ratio: float
    prompt_rate_observable_per_hour: float | None
    feedback_sessions: int
    feedback_coverage: float
    feedback_driver_coverage: float
    rating_mean_respondents_only: float | None
    consent_scope_breakdown: dict[str, int]
    reviewed_sessions: int
    review_coverage: float
    reviewers: int
    label_distribution: dict[str, int]
    unclear_label_ratio: float
    double_annotated_sessions: int
    annotation_pair_agreement: float | None
    version_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)
    driver_level_prompt_rate_mean: float | None = None
    caveats: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()

    def data_hash(self) -> str:
        payload = json.dumps(
            {k: v for k, v in self.to_dict().items() if k != "caveats"},
            ensure_ascii=False, sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


CAVEATS = [
    "提示率反映系统提示发生频率，不等同于驾驶员注意力缺失率。",
    "摄像头不可观测仅表示系统未读取到眼部信号，不能直接判定驾驶员未注视道路。",
    "反馈为自愿样本，评分结论仅适用于回复者，不得外推至未回复用户。",
    "同一驾驶员可贡献多段会话，驾驶员口径指标用于避免重复测量被当作独立样本。",
]


def _round(value: float, digits: int = 4) -> float:
    return round(value, digits)


def compute_condition_metrics(
    condition_id: str,
    taxonomy: Taxonomy,
    sessions: list[Session],
    feedback: list[FeedbackResponse],
    annotations: list[Annotation],
) -> ConditionMetrics:
    members = [
        s for s in sessions
        if taxonomy.match(s.wear, s.lighting) == condition_id
    ]
    feedback_by_session: dict[str, list[FeedbackResponse]] = {}
    for fb in feedback:
        feedback_by_session.setdefault(fb.session_id, []).append(fb)
    annotations_by_session: dict[str, list[Annotation]] = {}
    for ann in annotations:
        annotations_by_session.setdefault(ann.session_id, []).append(ann)

    member_ids = {s.session_id for s in members}
    total = len(members)
    distinct_drivers = len({s.driver_key for s in members})
    minutes_total = sum(s.task_minutes for s in members)
    prompts_total = sum(s.prompt_count for s in members)
    prompt_rate = (prompts_total / minutes_total * 60.0) if minutes_total else 0.0

    camera_breakdown = {"available": 0, "occluded": 0, "unknown": 0}
    for s in members:
        camera_breakdown[s.camera_state] += 1
    unobservable = sum(
        1 for s in members if s.camera_state in UNOBSERVABLE_CAMERA
    )
    observable_sessions = camera_breakdown["available"]
    unobservable_ratio = (unobservable / total) if total else 0.0

    with_prompt = sum(1 for s in members if s.prompt_count > 0)
    observable_minutes = sum(
        s.task_minutes for s in members if s.camera_state == "available"
    )
    observable_prompts = sum(
        s.prompt_count for s in members if s.camera_state == "available"
    )
    observable_rate = (
        _round(observable_prompts / observable_minutes * 60.0)
        if observable_minutes else None
    )

    # ---- 自愿反馈：仅描述回复者 ----
    covered_fb = [
        fb for sid in member_ids for fb in feedback_by_session.get(sid, [])
    ]
    feedback_sessions = len({fb.session_id for fb in covered_fb})
    feedback_coverage = (feedback_sessions / total) if total else 0.0
    feedback_drivers = len({fb.driver_key for fb in covered_fb})
    feedback_driver_coverage = (
        feedback_drivers / distinct_drivers if distinct_drivers else 0.0
    )
    ratings = [fb.rating for fb in covered_fb]
    rating_mean = _round(sum(ratings) / len(ratings), 2) if ratings else None
    consent_breakdown = {"retention-ok": 0, "research-only": 0}
    for fb in covered_fb:
        consent_breakdown[fb.consent_scope] += 1

    # ---- 人工复核 ----
    member_annotations = [
        ann for sid in member_ids for ann in annotations_by_session.get(sid, [])
    ]
    reviewed_sessions = len(member_ids & set(annotations_by_session))
    review_coverage = (reviewed_sessions / total) if total else 0.0
    reviewers = len({ann.reviewer for ann in member_annotations})
    label_distribution = {"attentive": 0, "away": 0, "unclear": 0}
    for ann in member_annotations:
        label_distribution[ann.attention_label] += 1
    n_labels = sum(label_distribution.values())
    unclear_ratio = (
        label_distribution["unclear"] / n_labels if n_labels else 0.0
    )

    # 双人（多人）一致性：同一片段上的成对标签一致比例
    double_sessions = [
        sid for sid in member_ids
        if len(annotations_by_session.get(sid, [])) >= 2
    ]
    pairs = 0
    agrees = 0
    for sid in double_sessions:
        labels = [ann.attention_label for ann in annotations_by_session[sid]]
        for a, b in combinations(labels, 2):
            pairs += 1
            if a == b:
                agrees += 1
    agreement = _round(agrees / pairs) if pairs else None

    # ---- 驾驶员口径：先在驾驶员内聚合，再取平均 ----
    per_driver: dict[str, list[Session]] = {}
    for s in members:
        per_driver.setdefault(s.driver_key, []).append(s)
    driver_rates = []
    for driver_sessions in per_driver.values():
        m = sum(x.task_minutes for x in driver_sessions)
        p = sum(x.prompt_count for x in driver_sessions)
        if m:
            driver_rates.append(p / m * 60.0)
    driver_level_mean = (
        _round(sum(driver_rates) / len(driver_rates)) if driver_rates else None
    )

    # ---- V14/V15 版本拆分 ----
    version_breakdown: dict[str, dict[str, Any]] = {}
    for version in ("V14", "V15"):
        vs = [s for s in members if s.version == version]
        if not vs:
            continue
        v_minutes = sum(s.task_minutes for s in vs)
        v_prompts = sum(s.prompt_count for s in vs)
        v_unobs = sum(1 for s in vs if s.camera_state in UNOBSERVABLE_CAMERA)
        version_breakdown[version] = {
            "sessions": len(vs),
            "distinct_drivers": len({s.driver_key for s in vs}),
            "prompt_rate_per_hour": _round(v_prompts / v_minutes * 60.0)
            if v_minutes else 0.0,
            "unobservable_ratio": _round(v_unobs / len(vs)) if vs else 0.0,
        }

    caveats = list(CAVEATS)
    if agreement is None:
        caveats.append("本条件缺少双人复核片段，复核一致性无法评估，不得视为已验证一致。")
    if feedback_coverage < 0.3 and total:
        caveats.append("反馈覆盖低于 30%，评分仅代表少数回复者。")

    return ConditionMetrics(
        condition_id=condition_id,
        taxonomy_version=taxonomy.version,
        sessions_total=total,
        distinct_drivers=distinct_drivers,
        minutes_total=minutes_total,
        prompts_total=prompts_total,
        prompt_rate_per_hour=_round(prompt_rate),
        observable_sessions=observable_sessions,
        unobservable_sessions=unobservable,
        unobservable_ratio=_round(unobservable_ratio),
        camera_breakdown=camera_breakdown,
        sessions_with_prompt_ratio=_round(with_prompt / total) if total else 0.0,
        prompt_rate_observable_per_hour=observable_rate,
        feedback_sessions=feedback_sessions,
        feedback_coverage=_round(feedback_coverage),
        feedback_driver_coverage=_round(feedback_driver_coverage),
        rating_mean_respondents_only=rating_mean,
        consent_scope_breakdown=consent_breakdown,
        reviewed_sessions=reviewed_sessions,
        review_coverage=_round(review_coverage),
        reviewers=reviewers,
        label_distribution=label_distribution,
        unclear_label_ratio=_round(unclear_ratio),
        double_annotated_sessions=len(double_sessions),
        annotation_pair_agreement=agreement,
        version_breakdown=version_breakdown,
        driver_level_prompt_rate_mean=driver_level_mean,
        caveats=caveats,
    )


def classify(metrics: ConditionMetrics, rules: EvidenceRules) -> tuple[str, list[str], str]:
    """返回 (分级, 原因列表, 建议动作)。"""
    reasons: list[str] = []

    if metrics.sessions_total == 0:
        reasons.append("该条件下没有任何会话资料。")
        return "unknown", reasons, "request_research"

    volume_ok = (
        metrics.sessions_total >= rules.min_sessions
        and metrics.distinct_drivers >= rules.min_distinct_drivers
    )
    if not volume_ok:
        reasons.append(
            f"样本量不足：会话 {metrics.sessions_total}/{rules.min_sessions}，"
            f"驾驶员 {metrics.distinct_drivers}/{rules.min_distinct_drivers}。"
        )

    if metrics.unobservable_ratio > rules.max_unobservable_ratio:
        reasons.append(
            f"不可观测比例 {metrics.unobservable_ratio:.0%} 超过阈值 "
            f"{rules.max_unobservable_ratio:.0%}，眼部信号缺口过大。"
        )

    feedback_ok = metrics.feedback_coverage >= rules.min_feedback_coverage
    if not feedback_ok:
        reasons.append(
            f"反馈覆盖 {metrics.feedback_coverage:.0%} 低于阈值 "
            f"{rules.min_feedback_coverage:.0%}，选择性反馈不足以代表整体。"
        )

    review_ok = metrics.reviewed_sessions >= rules.min_reviewed_sessions
    if not review_ok:
        reasons.append(
            f"人工复核片段 {metrics.reviewed_sessions}/{rules.min_reviewed_sessions} 不足。"
        )

    agreement_ok = False
    if metrics.annotation_pair_agreement is None:
        reasons.append("缺少双人复核样本，复核一致性无法评估。")
    elif metrics.double_annotated_sessions < rules.min_agreement_pairs:
        reasons.append(
            f"双人复核片段 {metrics.double_annotated_sessions} 少于 "
            f"{rules.min_agreement_pairs}，一致性证据不充分。"
        )
    elif metrics.annotation_pair_agreement < rules.min_agreement:
        reasons.append(
            f"复核一致性 {metrics.annotation_pair_agreement:.0%} 低于 "
            f"{rules.min_agreement:.0%}。"
        )
    else:
        agreement_ok = True

    if metrics.unclear_label_ratio > rules.max_unclear_label_ratio:
        reasons.append(
            f"标注 unclear 占比 {metrics.unclear_label_ratio:.0%} 超过 "
            f"{rules.max_unclear_label_ratio:.0%}，需要复核澄清（unclear 不等于 away）。"
        )

    if volume_ok and feedback_ok and review_ok and agreement_ok and not reasons:
        return "sufficient", ["各项阈值均满足，证据充分。"], "continue_monitoring"

    # 样本量或反馈覆盖属于根本性缺口 -> 需要针对性研究
    if not volume_ok or not feedback_ok:
        return "needs_research", reasons, "request_research"

    # 有基础样本但复核/不可观测/一致性存在疑问 -> 先复核
    return "needs_review", reasons, "continue_monitoring"


def compute_all(
    taxonomy: Taxonomy,
    sessions: list[Session],
    feedback: list[FeedbackResponse],
    annotations: list[Annotation],
    rules: EvidenceRules | None = None,
) -> dict[str, dict[str, Any]]:
    rules = rules or EvidenceRules()
    out: dict[str, dict[str, Any]] = {}
    for condition_id in taxonomy.conditions:
        metrics = compute_condition_metrics(
            condition_id, taxonomy, sessions, feedback, annotations
        )
        classification, reasons, action = classify(metrics, rules)
        out[condition_id] = {
            "metrics": metrics,
            "classification": classification,
            "reasons": reasons,
            "suggested_action": action,
        }
    return out


def diff_metrics(old: ConditionMetrics, new: ConditionMetrics) -> dict[str, Any]:
    """两次快照间的关键变化，用于判断哪些决策受到新结果影响。"""
    keys = (
        "prompt_rate_per_hour",
        "unobservable_ratio",
        "feedback_coverage",
        "review_coverage",
        "annotation_pair_agreement",
        "sessions_total",
    )
    changes: dict[str, Any] = {}
    for key in keys:
        old_v = getattr(old, key)
        new_v = getattr(new, key)
        if old_v != new_v:
            changes[key] = {"old": old_v, "new": new_v}
    return changes
