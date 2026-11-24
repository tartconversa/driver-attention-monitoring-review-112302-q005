"""本地 JSON 状态库与哈希链审计日志。

- 输入 CSV 永远只读，运营状态全部保存在独立的 state 目录；
- 每次状态变更先追加哈希链日志（journal.jsonl），再物化 state.json；
- 日志可独立重算校验，支持「为什么未知 / 谁接受了计划 / 哪条新结果改变了决策」追溯。
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Callable

from .importer import content_hash
from .models import (
    Annotation,
    BusinessRuleError,
    CollectedMaterial,
    Decision,
    EvidenceSnapshot,
    FeedbackResponse,
    ResearchPlan,
    ResearchSlot,
    Session,
    ValidationError,
)
from .taxonomy import (
    DEFAULT_TAXONOMY,
    build_mapping,
    get_taxonomy,
    is_many_to_one,
    now_utc,
)

STATE_FILE = "state.json"
JOURNAL_FILE = "journal.jsonl"
STORE_VERSION = 1


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(prev: str, entry: dict[str, Any]) -> str:
    body = _canonical(
        {
            "seq": entry["seq"],
            "ts": entry["ts"],
            "actor": entry["actor"],
            "action": entry["action"],
            "payload": entry["payload"],
            "prev_hash": prev,
        }
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class Store:
    """应用状态库。所有写操作都会留下不可跳过的审计条目。"""

    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.state_path = self.dir / STATE_FILE
        self.journal_path = self.dir / JOURNAL_FILE
        self.state: dict[str, Any] = {}

    # ---------- 生命周期 ----------

    @classmethod
    def create(cls, state_dir: str | Path) -> "Store":
        directory = Path(state_dir)
        directory.mkdir(parents=True, exist_ok=True)
        store = cls(directory)
        if store.state_path.exists():
            raise FileExistsError(f"状态库已存在：{store.state_path}")
        store.state = {
            "store_version": STORE_VERSION,
            "active_taxonomy": DEFAULT_TAXONOMY,
            "taxonomy_events": [],
            "sessions": {},
            "feedback": {},
            "annotations": {},
            "slots": {},
            "imports": [],
            "snapshots": [],
            "decisions": [],
            "plans": [],
            "slot_events": [],
            "materials": [],
            "counters": {},
        }
        store._append(
            "system", "store.create", {"active_taxonomy": DEFAULT_TAXONOMY}
        )
        store._flush()
        return store

    @classmethod
    def open(cls, state_dir: str | Path) -> "Store":
        store = cls(state_dir)
        if not store.state_path.exists():
            raise FileNotFoundError(f"状态库不存在：{store.state_path}")
        store.state = json.loads(store.state_path.read_text(encoding="utf-8"))
        ok, msg = store.verify_journal()
        if not ok:
            raise BusinessRuleError(f"审计日志校验失败：{msg}")
        return store

    def _flush(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.state_path)

    def _next_id(self, prefix: str) -> str:
        n = self.state["counters"].get(prefix, 0) + 1
        self.state["counters"][prefix] = n
        return f"{prefix}{n:03d}"

    # ---------- 审计日志 ----------

    def _append(self, actor: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        seq = sum(1 for _ in self.journal_path.open(encoding="utf-8")) if self.journal_path.exists() else 0
        entry = {
            "seq": seq + 1,
            "ts": now_utc(),
            "actor": actor,
            "action": action,
            "payload": payload,
        }
        prev = self._last_hash()
        entry["prev_hash"] = prev
        entry["entry_hash"] = _digest(prev, entry)
        with self.journal_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
        return entry

    def _last_hash(self) -> str:
        if not self.journal_path.exists():
            return "0" * 64
        last = self.journal_path.read_text(encoding="utf-8").strip().splitlines()[-1]
        return json.loads(last)["entry_hash"]

    def verify_journal(self) -> tuple[bool, str]:
        """重算整条哈希链，返回 (是否通过, 说明)。"""
        if not self.journal_path.exists():
            return False, "journal.jsonl 不存在"
        prev = "0" * 64
        for line_no, line in enumerate(
            self.journal_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            entry = json.loads(line)
            expected = _digest(prev, entry)
            if entry["entry_hash"] != expected:
                return False, f"第 {line_no} 条日志哈希不匹配"
            if entry["prev_hash"] != prev:
                return False, f"第 {line_no} 条日志前驱哈希断裂"
            prev = entry["entry_hash"]
        return True, f"共 {line_no} 条日志，哈希链完整"

    def journal(self) -> list[dict[str, Any]]:
        if not self.journal_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.journal_path.read_text(encoding="utf-8").splitlines()
        ]

    # ---------- 持续导入（幂等） ----------

    def import_bundle(
        self,
        bundle: dict[str, list],
        source_dir: str,
        actor: str = "ingest",
    ) -> dict[str, dict[str, int]]:
        """按内容哈希去重导入；已存在的行跳过而不是重复计数。"""
        summary: dict[str, dict[str, int]] = {}
        targets: list[tuple[str, list[Any], str, Callable[[Any], str]]] = [
            ("sessions", bundle["sessions"], "session_id", lambda o: o.session_id),
            ("feedback", bundle["feedback"], "response_id", lambda o: o.response_id),
            ("annotations", bundle["annotations"], "annotation_id", lambda o: o.annotation_id),
            ("slots", bundle["slots"], "slot_id", lambda o: o.slot_id),
        ]
        for table, records, id_key, get_id in targets:
            new_count = skipped = 0
            new_hashes: list[str] = []
            for record in records:
                row = record.to_dict()
                h = content_hash({k: str(v) for k, v in row.items()})
                existing = self.state[table]
                if h in {item["_hash"] for item in existing.values()}:
                    skipped += 1
                    continue
                rid = get_id(record)
                if rid in existing:
                    # 同主键但内容变化：保留历史，作为修订导入
                    raise ValidationError(
                        f"{table} 中 {id_key}={rid} 已存在但内容不同，拒绝覆盖历史资料"
                    )
                row["_hash"] = h
                existing[rid] = row
                new_hashes.append(h)
                new_count += 1
            summary[table] = {"rows": len(records), "new": new_count, "skipped": skipped}
        self.state["imports"].append(
            {"ts": now_utc(), "source_dir": str(source_dir), "actor": actor, "files": summary}
        )
        self._append(actor, "data.import", {"source_dir": str(source_dir), "summary": summary})
        self._flush()
        return summary

    # ---------- 读取 ----------

    def _hydrate(self, table: str, cls: type) -> list[Any]:
        return [
            cls(**{k: v for k, v in row.items() if k != "_hash"})
            for row in self.state[table].values()
        ]

    def sessions(self) -> list[Session]:
        return self._hydrate("sessions", Session)

    def feedback(self) -> list[FeedbackResponse]:
        return self._hydrate("feedback", FeedbackResponse)

    def annotations(self) -> list[Annotation]:
        return self._hydrate("annotations", Annotation)

    def slots(self) -> list[ResearchSlot]:
        return self._hydrate("slots", ResearchSlot)

    def slot_state(self, slot_id: str) -> dict[str, Any]:
        """派生名额状态：CSV 基线状态 + 运营事件。"""
        baseline = self.state["slots"][slot_id]
        state = baseline["status"]
        events = [e for e in self.state["slot_events"] if e["slot_id"] == slot_id]
        for event in events:
            state = event["to_state"]
        return {"slot_id": slot_id, "baseline": baseline["status"], "state": state,
                "consent_state": baseline["consent_state"], "capacity": baseline["capacity"],
                "events": events}

    def open_slot_ids(self) -> set[str]:
        return {
            sid
            for sid in self.state["slots"]
            if self.slot_state(sid)["state"] == "open"
        }

    # ---------- 分类法切换与结论适用范围重标 ----------

    def activate_taxonomy(self, version: str, actor: str, note: str) -> dict[str, Any]:
        if version == self.state["active_taxonomy"]:
            raise BusinessRuleError(f"分类法 {version} 已是当前版本")
        get_taxonomy(version)  # 校验存在
        old = self.state["active_taxonomy"]
        mapping = build_mapping(old, version)
        merged = is_many_to_one(old, version)
        remarks: list[str] = []
        for snap in self.state["snapshots"]:
            if snap["taxonomy_version"] != old or snap["applicability"] != "current":
                continue
            new_id = mapping[snap["condition_id"]]
            snap["applicability"] = "superseded"
            snap["superseded_by"] = new_id
            tag = "合并沿用(需复核)" if merged.get(new_id) else "映射沿用(需复核)"
            remarks.append(
                f"{snap['snapshot_id']} 条件 {snap['condition_id']} → {new_id}（{tag}）"
            )
            # 为新条件生成 carried_over 占位快照，保留原证据但不视为最终结论
            carried = EvidenceSnapshot(
                snapshot_id=self._next_id("ES-"),
                ts=now_utc(),
                taxonomy_version=version,
                condition_id=new_id,
                classification=snap["classification"],
                reasons=snap["reasons"] + [f"由 {snap['snapshot_id']} 跨版本沿用，待复核确认"],
                metrics=snap["metrics"],
                data_hash=snap["data_hash"],
                applicability="carried_over",
                superseded_by=None,
            )
            self.state["snapshots"].append(carried.to_dict())
        self.state["active_taxonomy"] = version
        self.state["taxonomy_events"].append(
            {"ts": now_utc(), "actor": actor, "from": old, "to": version, "note": note}
        )
        self._append(
            actor,
            "taxonomy.activate",
            {"from": old, "to": version, "note": note, "remarks": remarks},
        )
        self._flush()
        return {"from": old, "to": version, "remarks": remarks}

    # ---------- 证据快照与决策 ----------

    def add_snapshot(self, snapshot: EvidenceSnapshot) -> None:
        self.state["snapshots"].append(snapshot.to_dict())
        self._append(
            "analyst",
            "snapshot.add",
            {"snapshot_id": snapshot.snapshot_id,
             "condition_id": snapshot.condition_id,
             "classification": snapshot.classification},
        )
        self._flush()

    def add_decision(self, decision: Decision) -> None:
        # 决策必须引用当前分类法下存在的快照
        refs = [
            s for s in self.state["snapshots"]
            if s["snapshot_id"] == decision.snapshot_id
            and s["taxonomy_version"] == decision.taxonomy_version
        ]
        if not refs:
            raise ValidationError("决策引用的证据快照不存在或分类法版本不一致")
        self.state["decisions"].append(decision.to_dict())
        self._append(
            decision.actor,
            "decision.add",
            {"decision_id": decision.decision_id, "condition_id": decision.condition_id,
             "action": decision.action, "snapshot_id": decision.snapshot_id},
        )
        self._flush()

    def mark_decisions_affected_by_change(self, condition_ids: set[str], actor: str) -> list[str]:
        """新证据出现后，把相关条件上仍有效的决策标记为受影响，等待负责人确认。"""
        affected: list[str] = []
        for decision in self.state["decisions"]:
            if decision["condition_id"] in condition_ids and not decision["affected_by_change"]:
                decision["affected_by_change"] = True
                affected.append(decision["decision_id"])
        if affected:
            self._append(actor, "decision.flag_affected", {"decision_ids": affected})
            self._flush()
        return affected

    # ---------- 研究计划与名额生命周期 ----------

    def create_plan(
        self,
        created_by: str,
        target_conditions: list[str],
        region: str | None,
        note: str,
    ) -> ResearchPlan:
        tax = get_taxonomy(self.state["active_taxonomy"])
        unknown = [c for c in target_conditions if c not in tax.conditions]
        if unknown:
            raise ValidationError(f"计划引用了当前分类法中不存在的条件：{unknown}")
        plan = ResearchPlan(
            plan_id=self._next_id("RP-"),
            ts=now_utc(),
            created_by=created_by,
            target_conditions=list(target_conditions),
            taxonomy_version=tax.version,
            region=region,
            note=note,
        )
        self.state["plans"].append(plan.to_dict())
        self._append(created_by, "plan.create", {"plan_id": plan.plan_id,
                                                 "conditions": target_conditions})
        self._flush()
        return plan

    def accept_plan(self, plan_id: str, slot_ids: list[str], accepted_by: str) -> ResearchPlan:
        plan = self._find_plan(plan_id)
        if plan["status"] != "proposed":
            raise BusinessRuleError(f"计划 {plan_id} 状态为 {plan['status']}，不可接受")
        if not slot_ids:
            raise ValidationError("接受计划时必须指定占用的研究名额")
        if len(set(slot_ids)) != len(slot_ids):
            raise ValidationError("名额列表存在重复")
        for sid in slot_ids:
            if sid not in self.state["slots"]:
                raise ValidationError(f"名额 {sid} 不存在")
            derived = self.slot_state(sid)
            if derived["state"] != "open":
                raise BusinessRuleError(f"名额 {sid} 状态为 {derived['state']}，无法占用")
            if derived["consent_state"] == "withdrawn":
                raise BusinessRuleError(f"名额 {sid} 同意已撤回，不可占用")
        for sid in slot_ids:
            self._slot_event(sid, "accept", "accepted", accepted_by,
                             {"plan_id": plan_id})
        plan["status"] = "accepted"
        plan["accepted_by"] = accepted_by
        plan["accepted_at"] = now_utc()
        plan["slot_ids"] = list(slot_ids)
        self._append(accepted_by, "plan.accept",
                     {"plan_id": plan_id, "slot_ids": slot_ids})
        self._flush()
        return ResearchPlan(**plan)

    def execute_slot(self, slot_id: str, consent_scope: str, actor: str,
                     detail: str = "") -> CollectedMaterial:
        """执行名额并登记已采集资料。只有 accepted 名额可执行。"""
        derived = self.slot_state(slot_id)
        if derived["state"] != "accepted":
            raise BusinessRuleError(f"名额 {slot_id} 状态为 {derived['state']}，不可执行")
        if derived["consent_state"] != "active":
            raise BusinessRuleError(
                f"名额 {slot_id} 同意状态为 {derived['consent_state']}，未取得有效同意，不能执行"
            )
        plan_id = next(
            (e["payload"]["plan_id"] for e in reversed(derived["events"])
             if e["kind"] == "accept"),
            None,
        )
        self._slot_event(slot_id, "execute", "done", actor, {"plan_id": plan_id})
        material = CollectedMaterial(
            material_id=self._next_id("MAT-"),
            slot_id=slot_id,
            plan_id=plan_id or "",
            collected_at=now_utc(),
            consent_scope=consent_scope,
            detail=detail,
        )
        self.state["materials"].append(material.to_dict())
        self._append(actor, "material.collect", material.to_dict())
        self._flush()
        return material

    def withdraw_consent(self, slot_id: str, actor: str) -> dict[str, Any]:
        """撤回同意：未执行名额释放；已采集资料按原授权范围保留或转移除。"""
        if slot_id not in self.state["slots"]:
            raise ValidationError(f"名额 {slot_id} 不存在")
        derived = self.slot_state(slot_id)
        result: dict[str, Any] = {"slot_id": slot_id, "prior_state": derived["state"],
                                  "released_capacity": 0, "materials": []}
        if derived["state"] in ("withdrawn", "device_failed"):
            raise BusinessRuleError(f"名额 {slot_id} 已终结（{derived['state']}），不能重复撤回")
        if derived["state"] == "done":
            # 已执行名额不释放容量；每次撤回都按资料各自的授权范围重新处置。
            # 新资料在 consent_state=withdrawn 下无法再采集（execute_slot 会拒绝）。
            pending = [
                m for m in self.state["materials"]
                if m["slot_id"] == slot_id
                and m["retention_state"] == "retained"
            ]
            if not pending:
                raise BusinessRuleError(
                    f"名额 {slot_id} 已执行且没有待处置资料，撤回不改变名额状态"
                )
            for material in pending:
                if material["consent_scope"] == "research-only":
                    material["retention_state"] = "removal_pending"
                    result["materials"].append(
                        {"material_id": material["material_id"],
                         "action": "removal_pending"})
                else:
                    result["materials"].append(
                        {"material_id": material["material_id"], "action": "retained"})
            self._append(actor, "consent.withdraw_materials", result)
        else:
            released = derived["capacity"]
            self._slot_event(slot_id, "withdraw", "withdrawn", actor,
                             {"released_capacity": released})
            result["released_capacity"] = released
            for plan in self.state["plans"]:
                if slot_id in plan["slot_ids"]:
                    plan["released_capacity"] += released
            self._append(actor, "consent.withdraw_slot", result)
        # 基线同意状态同步为 withdrawn，保留历史于审计日志
        self.state["slots"][slot_id]["consent_state"] = "withdrawn"
        self._flush()
        return result

    def device_failure(self, slot_id: str, actor: str) -> dict[str, Any]:
        """设备失效：只能释放尚未执行的名额。"""
        derived = self.slot_state(slot_id)
        if derived["state"] == "done":
            raise BusinessRuleError(f"名额 {slot_id} 已执行，设备失效不能撤回已采集资料")
        if derived["state"] in ("withdrawn", "device_failed"):
            raise BusinessRuleError(f"名额 {slot_id} 已处于 {derived['state']}")
        released = derived["capacity"]
        self._slot_event(slot_id, "device_fail", "device_failed", actor,
                         {"released_capacity": released})
        for plan in self.state["plans"]:
            if slot_id in plan["slot_ids"]:
                plan["released_capacity"] += released
        self._append(actor, "slot.device_failure",
                     {"slot_id": slot_id, "released_capacity": released})
        self._flush()
        return {"slot_id": slot_id, "released_capacity": released}

    def remove_material(self, material_id: str, actor: str) -> CollectedMaterial:
        """对 removal_pending 的资料执行移除。"""
        for material in self.state["materials"]:
            if material["material_id"] == material_id:
                if material["retention_state"] != "removal_pending":
                    raise BusinessRuleError(
                        f"资料 {material_id} 状态为 {material['retention_state']}，不可移除"
                    )
                material["retention_state"] = "removed"
                self._append(actor, "material.remove", {"material_id": material_id})
                self._flush()
                return CollectedMaterial(**material)
        raise ValidationError(f"资料 {material_id} 不存在")

    def _slot_event(self, slot_id: str, kind: str, to_state: str,
                    actor: str, payload: dict[str, Any]) -> None:
        event = {"ts": now_utc(), "slot_id": slot_id, "kind": kind,
                 "to_state": to_state, "actor": actor, "payload": payload}
        self.state["slot_events"].append(event)

    def _find_plan(self, plan_id: str) -> dict[str, Any]:
        for plan in self.state["plans"]:
            if plan["plan_id"] == plan_id:
                return plan
        raise ValidationError(f"研究计划 {plan_id} 不存在")

    def plans(self) -> list[ResearchPlan]:
        return [ResearchPlan(**p) for p in self.state["plans"]]

    def materials(self) -> list[CollectedMaterial]:
        return [CollectedMaterial(**m) for m in self.state["materials"]]

    def snapshots(self) -> list[EvidenceSnapshot]:
        return [EvidenceSnapshot(**s) for s in self.state["snapshots"]]

    def decisions(self) -> list[Decision]:
        return [Decision(**d) for d in self.state["decisions"]]

    # ---------- 追溯 ----------

    def condition_trace(self, condition_id: str) -> dict[str, Any]:
        """汇总一个条件的全部证据、决策、研究安排，回答「为什么仍未知」。"""
        snaps = [s for s in self.state["snapshots"] if s["condition_id"] == condition_id]
        decisions = [d for d in self.state["decisions"]
                     if d["condition_id"] == condition_id]
        plan_links = [
            p for p in self.state["plans"]
            if condition_id in p["target_conditions"]
        ]
        return {
            "condition_id": condition_id,
            "taxonomy": self.state["active_taxonomy"],
            "snapshots": snaps,
            "decisions": decisions,
            "plans": plan_links,
        }
