"""中文报告生成：分层证据报告、名额状态、追溯说明与口径护栏。"""

from __future__ import annotations

from typing import Any

from .metrics import ConditionMetrics
from .models import EvidenceSnapshot
from .taxonomy import Taxonomy

CLASSIFICATION_TEXT = {
    "sufficient": "证据充分",
    "needs_review": "需要复核",
    "needs_research": "需要针对性研究",
    "unknown": "未知（无资料）",
}

ACTION_TEXT = {
    "continue_monitoring": "继续观察",
    "adjust_copy": "调整产品说明",
    "request_research": "申请针对性研究",
}

APPLICABILITY_TEXT = {
    "current": "当前适用",
    "carried_over": "跨版本沿用（待复核确认）",
    "superseded": "已被新分类法取代",
}


def _pct(value: float | None) -> str:
    return "无法评估" if value is None else f"{value:.1%}"


def condition_report(taxonomy: Taxonomy, result: dict[str, Any]) -> str:
    metrics: ConditionMetrics = result["metrics"]
    cid = metrics.condition_id
    lines = [
        f"## 条件 {cid}：{taxonomy.condition_label(cid)}",
        f"- 证据分级：**{CLASSIFICATION_TEXT[result['classification']]}**"
        f"（建议动作：{ACTION_TEXT[result['suggested_action']]}）",
        f"- 样本：会话 {metrics.sessions_total} 段，驾驶员 {metrics.distinct_drivers} 人，"
        f"总时长 {metrics.minutes_total} 分钟",
        f"- 提示率（全部会话）：{metrics.prompt_rate_per_hour:.2f} 次/小时；"
        f"驾驶员口径平均："
        + (f"{metrics.driver_level_prompt_rate_mean:.2f} 次/小时"
           if metrics.driver_level_prompt_rate_mean is not None
           else "无法评估"),
        f"- 仅摄像头可用会话的提示率："
        + (f"{metrics.prompt_rate_observable_per_hour:.2f} 次/小时"
           if metrics.prompt_rate_observable_per_hour is not None
           else "无法评估（没有摄像头可用的会话）"),
        f"- 出现至少一次提示的会话占比：{_pct(metrics.sessions_with_prompt_ratio)}",
        f"- 不可观测：{metrics.unobservable_sessions} 段，占 {_pct(metrics.unobservable_ratio)}"
        f"（摄像头分布 {metrics.camera_breakdown}）",
        f"- 反馈覆盖：{metrics.feedback_sessions}/{metrics.sessions_total} 个会话"
        f"（{_pct(metrics.feedback_coverage)}），驾驶员覆盖 {_pct(metrics.feedback_driver_coverage)}"
        f"，回复者平均评分："
        + (f"{metrics.rating_mean_respondents_only}"
           if metrics.rating_mean_respondents_only is not None else "无回复"),
        f"- 授权范围分布（仅回复者）：{metrics.consent_scope_breakdown}",
        f"- 人工复核：覆盖 {metrics.reviewed_sessions} 段（{_pct(metrics.review_coverage)}），"
        f"标注员 {metrics.reviewers} 人，标签分布 {metrics.label_distribution}，"
        f"unclear 占比 {_pct(metrics.unclear_label_ratio)}",
        f"- 双注片段 {metrics.double_annotated_sessions} 个，"
        f"成对一致性：{_pct(metrics.annotation_pair_agreement)}",
        "- 版本拆分：",
    ]
    for version, breakdown in metrics.version_breakdown.items():
        lines.append(
            f"  - {version}：{breakdown['sessions']} 段 / {breakdown['distinct_drivers']} 人，"
            f"提示率 {breakdown['prompt_rate_per_hour']:.2f} 次/小时，"
            f"不可观测 {_pct(breakdown['unobservable_ratio'])}"
        )
    lines.append("- 分级原因：")
    for reason in result["reasons"]:
        lines.append(f"  - {reason}")
    lines.append("- 口径护栏：")
    for caveat in metrics.caveats:
        lines.append(f"  - {caveat}")
    return "\n".join(lines)


def full_report(taxonomy: Taxonomy, results: dict[str, dict[str, Any]]) -> str:
    order = ["sufficient", "needs_review", "needs_research", "unknown"]
    lines = [
        "# 驾驶员注意力监测分层体验报告",
        f"分类法版本：{taxonomy.version}（{taxonomy.description}）",
        "",
        "## 汇总表",
        "| 条件 | 分级 | 会话 | 驾驶员 | 提示率/小时 | 不可观测 | 反馈覆盖 | 复核覆盖 | 一致性 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    ranked = sorted(
        results.items(),
        key=lambda kv: (order.index(kv[1]["classification"]), kv[0]),
    )
    for cid, result in ranked:
        m: ConditionMetrics = result["metrics"]
        lines.append(
            f"| {cid} | {CLASSIFICATION_TEXT[result['classification']]} | "
            f"{m.sessions_total} | {m.distinct_drivers} | "
            f"{m.prompt_rate_per_hour:.2f} | {_pct(m.unobservable_ratio)} | "
            f"{_pct(m.feedback_coverage)} | {_pct(m.review_coverage)} | "
            f"{_pct(m.annotation_pair_agreement)} |"
        )
    lines.append("")
    for cid, result in ranked:
        lines.append(condition_report(taxonomy, result))
        lines.append("")
    lines.append(
        "> 重要声明：提示次数不是注意力缺失次数；系统未读取到眼部信号不等于"
        "驾驶员未注视道路；反馈为自愿样本，不得外推至未回复用户；"
        "公开报道不含独立测试数据，本报告仅基于本地合成资料。"
    )
    return "\n".join(lines)


def snapshot_listing(snapshots: list[EvidenceSnapshot]) -> str:
    lines = ["# 证据快照与适用范围",
             "| 快照 | 分类法 | 条件 | 分级 | 适用范围 | 取代关系 | 时间 |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for snap in snapshots:
        lines.append(
            f"| {snap.snapshot_id} | {snap.taxonomy_version} | {snap.condition_id} | "
            f"{CLASSIFICATION_TEXT.get(snap.classification, snap.classification)} | "
            f"{APPLICABILITY_TEXT[snap.applicability]} | "
            f"{snap.superseded_by or '—'} | {snap.ts} |"
        )
    return "\n".join(lines)


def trace_report(trace: dict[str, Any]) -> str:
    lines = [
        f"# 条件 {trace['condition_id']} 追溯（当前分类法 {trace['taxonomy']}）",
        "",
        "## 证据快照链",
    ]
    for snap in trace["snapshots"]:
        lines.append(
            f"- {snap['snapshot_id']} [{snap['taxonomy_version']}] "
            f"{CLASSIFICATION_TEXT.get(snap['classification'], snap['classification'])} / "
            f"{APPLICABILITY_TEXT[snap['applicability']]} @ {snap['ts']}"
        )
        for reason in snap["reasons"]:
            lines.append(f"    - {reason}")
    lines.append("\n## 决策记录")
    if not trace["decisions"]:
        lines.append("- 暂无决策；若该条件仍属未知，请对照上方快照的分级原因逐条排查。")
    for decision in trace["decisions"]:
        flag = "（已被后续结果标记为受影响，需复核）" if decision["affected_by_change"] else ""
        lines.append(
            f"- {decision['decision_id']} {decision['ts']} 负责人 {decision['actor']}："
            f"{ACTION_TEXT.get(decision['action'], decision['action'])}"
            f"（依据 {decision['snapshot_id']}）{flag} — {decision['note']}"
        )
    lines.append("\n## 研究安排")
    if not trace["plans"]:
        lines.append("- 暂无关联研究计划。")
    for plan in trace["plans"]:
        lines.append(
            f"- {plan['plan_id']} 状态 {plan['status']}，创建人 {plan['created_by']}，"
            f"接受人 {plan.get('accepted_by') or '—'}，名额 {plan['slot_ids'] or '—'}，"
            f"已释放容量 {plan['released_capacity']}"
        )
    return "\n".join(lines)


def slots_report(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# 研究名额状态",
        "| 名额 | 基线状态 | 当前状态 | 同意状态 | 容量 | 事件数 |",
        "| --- | --- | --- | --- | ---: | ---: |",
    ]
    for row in sorted(rows, key=lambda r: r["slot_id"]):
        lines.append(
            f"| {row['slot_id']} | {row['baseline']} | {row['state']} | "
            f"{row['consent_state']} | {row['capacity']} | {len(row['events'])} |"
        )
    lines.append("")
    lines.append("撤回同意或设备失效只能释放未执行部分；已执行名额的资料按原授权范围保留或移除。")
    return "\n".join(lines)
