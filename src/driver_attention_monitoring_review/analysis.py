"""分析服务：把指标计算结果落为证据快照，并检测新结果对既有决策的影响。"""

from __future__ import annotations

from .metrics import (
    ConditionMetrics,
    EvidenceRules,
    compute_all,
    diff_metrics,
)
from .models import EvidenceSnapshot
from .store import Store
from .taxonomy import get_taxonomy, now_utc


def run_analysis(
    store: Store,
    rules: EvidenceRules | None = None,
    actor: str = "analyst",
) -> dict[str, Any]:
    """对当前分类法下每个条件生成一份新的证据快照。

    - 与该条件上一版 current 快照比较，记录关键指标变化；
    - 若指标发生变化，相关决策会被标记 affected_by_change；
    - carried_over/superseded 快照保持原样，不参与覆盖。
    """
    rules = rules or EvidenceRules()
    taxonomy = get_taxonomy(store.state["active_taxonomy"])
    results = compute_all(
        taxonomy, store.sessions(), store.feedback(),
        store.annotations(), rules,
    )
    changed_conditions: set[str] = set()
    new_snapshots: list[EvidenceSnapshot] = []
    for cid, result in results.items():
        metrics: ConditionMetrics = result["metrics"]
        previous = _last_current_snapshot(store, cid)
        changes = (
            diff_metrics(previous, metrics) if previous is not None else {}
        )
        if changes:
            changed_conditions.add(cid)
        snapshot = EvidenceSnapshot(
            snapshot_id=store._next_id("ES-"),
            ts=now_utc(),
            taxonomy_version=taxonomy.version,
            condition_id=cid,
            classification=result["classification"],
            reasons=result["reasons"] + (
                [f"相比上一版快照的变化：{changes}"] if changes else []
            ),
            metrics=metrics.to_dict(),
            data_hash=metrics.data_hash(),
            applicability="current",
        )
        store.add_snapshot(snapshot)
        new_snapshots.append(snapshot)

    affected = store.mark_decisions_affected_by_change(changed_conditions, actor)
    return {
        "taxonomy": taxonomy.version,
        "results": results,
        "snapshots": new_snapshots,
        "changed_conditions": sorted(changed_conditions),
        "affected_decisions": affected,
    }


def _last_current_snapshot(store: Store, condition_id: str) -> ConditionMetrics | None:
    candidates = [
        s for s in store.state["snapshots"]
        if s["condition_id"] == condition_id
        and s["applicability"] == "current"
        and s["taxonomy_version"] == store.state["active_taxonomy"]
    ]
    if not candidates:
        return None
    return ConditionMetrics(**candidates[-1]["metrics"])
