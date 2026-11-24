"""研究名额生命周期、授权处置与审计追溯测试。"""

from __future__ import annotations

import unittest
from pathlib import Path

from _helpers import make_slot, temp_dir

from driver_attention_monitoring_review.models import BusinessRuleError, ValidationError
from driver_attention_monitoring_review.store import Store


class StoreLifecycleTests(unittest.TestCase):
    def _store_with_slots(self, slots: dict[str, str] | None = None):
        tmp = temp_dir()
        self.addCleanup(tmp.cleanup)
        store = Store.create(Path(tmp.name) / "state")
        definitions = slots or {
            "RS-01": ("open", "active"),
            "RS-02": ("open", "pending"),
            "RS-03": ("accepted", "active"),
            "RS-04": ("done", "active"),
            "RS-05": ("open", "withdrawn"),
        }
        for sid, (status, consent) in definitions.items():
            row = make_slot(sid, status=status, consent=consent, capacity=3)
            store.state["slots"][sid] = {**row.to_dict(), "_hash": sid}
        store._flush()
        return store

    def test_journal_hash_chain_verifies(self):
        store = self._store_with_slots()
        ok, msg = store.verify_journal()
        self.assertTrue(ok, msg)

    def test_tampering_is_detected(self):
        store = self._store_with_slots()
        journal = store.journal_path
        lines = journal.read_text(encoding="utf-8").splitlines()
        import json
        entry = json.loads(lines[0])
        entry["actor"] = "intruder"
        lines[0] = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        journal.write_text("\n".join(lines) + "\n", encoding="utf-8")
        ok, msg = store.verify_journal()
        self.assertFalse(ok)
        self.assertIn("哈希", msg)

    def test_accept_plan_occupies_only_open_consented_slots(self):
        store = self._store_with_slots()
        plan = store.create_plan("lead", ["C1-hat-day"], None, "补充帽檐日间证据")
        # withdrawn 名额不能占用
        with self.assertRaises(BusinessRuleError):
            store.accept_plan(plan.plan_id, ["RS-05"], "lead")
        # 非 open 名额不能占用
        with self.assertRaises(BusinessRuleError):
            store.accept_plan(plan.plan_id, ["RS-03"], "lead")
        accepted = store.accept_plan(plan.plan_id, ["RS-01", "RS-02"], "lead")
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(accepted.accepted_by, "lead")
        self.assertEqual(store.slot_state("RS-01")["state"], "accepted")
        # 重复占用被拒绝
        with self.assertRaises(BusinessRuleError):
            plan2 = store.create_plan("lead", ["C1-hat-day"], None, "另一个计划")
            store.accept_plan(plan2.plan_id, ["RS-01"], "lead")

    def test_execute_requires_active_consent_and_accepted_state(self):
        store = self._store_with_slots()
        # open 不能直接执行
        with self.assertRaises(BusinessRuleError):
            store.execute_slot("RS-01", "retention-ok", "coord")
        plan = store.create_plan("coord", ["C1-hat-day"], None, "x")
        store.accept_plan(plan.plan_id, ["RS-01", "RS-02"], "lead")
        # pending 同意不能执行
        with self.assertRaises(BusinessRuleError):
            store.execute_slot("RS-02", "retention-ok", "coord")
        material = store.execute_slot("RS-01", "research-only", "coord")
        self.assertEqual(store.slot_state("RS-01")["state"], "done")
        self.assertEqual(material.consent_scope, "research-only")

    def test_withdraw_before_execution_releases_capacity(self):
        store = self._store_with_slots()
        plan = store.create_plan("coord", ["C1-hat-day"], None, "x")
        store.accept_plan(plan.plan_id, ["RS-01"], "lead")
        result = store.withdraw_consent("RS-01", "driver")
        self.assertEqual(result["released_capacity"], 3)
        self.assertEqual(store.slot_state("RS-01")["state"], "withdrawn")
        refreshed = next(p for p in store.state["plans"]
                         if p["plan_id"] == plan.plan_id)
        self.assertEqual(refreshed["released_capacity"], 3)

    def test_withdraw_after_execution_keeps_retained_material_and_flags_removal(self):
        store = self._store_with_slots()
        plan = store.create_plan("coord", ["C1-hat-day"], None, "x")
        # RS-04 基线为 done 且 consent active，准备两份已采集资料
        store.state["materials"].append({
            "material_id": "MAT-009", "slot_id": "RS-04", "plan_id": plan.plan_id,
            "collected_at": "2026-12-01T00:00:00Z",
            "consent_scope": "research-only",
            "retention_state": "retained", "detail": "",
        })
        store.state["materials"].append({
            "material_id": "MAT-010", "slot_id": "RS-04", "plan_id": plan.plan_id,
            "collected_at": "2026-12-01T00:00:00Z",
            "consent_scope": "retention-ok",
            "retention_state": "retained", "detail": "",
        })
        store._flush()

        # 已执行名额撤回不释放容量；research-only 转 removal_pending，retention-ok 保留
        result = store.withdraw_consent("RS-04", "driver")
        self.assertEqual(result["released_capacity"], 0)
        self.assertEqual(store.slot_state("RS-04")["state"], "done")
        actions = {item["material_id"]: item["action"]
                   for item in result["materials"]}
        self.assertEqual(actions["MAT-009"], "removal_pending")
        self.assertEqual(actions["MAT-010"], "retained")

        # research-only 资料移除后，仅剩按授权保留的资料，再次撤回只得到保留动作
        removed = store.remove_material("MAT-009", "coord")
        self.assertEqual(removed.retention_state, "removed")
        repeat = store.withdraw_consent("RS-04", "driver")
        self.assertEqual(
            {item["action"] for item in repeat["materials"]}, {"retained"}
        )
        # 全部资料处理完后（保留资料也已挂起），再次撤回应拒绝空操作
        for material in store.state["materials"]:
            if material["slot_id"] == "RS-04":
                material["retention_state"] = "removed"
        store._flush()
        with self.assertRaises(BusinessRuleError):
            store.withdraw_consent("RS-04", "driver")
        # 已移除资料不能重复移除
        with self.assertRaises(BusinessRuleError):
            store.remove_material("MAT-009", "coord")

        # 撤回后不能再向该名额采集新资料
        with self.assertRaises(BusinessRuleError):
            store.execute_slot("RS-04", "retention-ok", "coord")

        # 另一条研究路径：accepted 名额正常执行后采集的资料默认可保留
        store.accept_plan(plan.plan_id, ["RS-01"], "lead")
        retained = store.execute_slot("RS-01", "retention-ok", "coord")
        self.assertEqual(store.slot_state("RS-01")["state"], "done")
        self.assertTrue(retained.material_id)

    def test_device_failure_only_releases_unexecuted_slots(self):
        store = self._store_with_slots()
        plan = store.create_plan("coord", ["C1-hat-day"], None, "x")
        store.accept_plan(plan.plan_id, ["RS-01"], "lead")
        result = store.device_failure("RS-01", "coord")
        self.assertEqual(result["released_capacity"], 3)
        self.assertEqual(store.slot_state("RS-01")["state"], "device_failed")
        # 已执行名额不能以设备失效为由释放
        with self.assertRaises(BusinessRuleError):
            store.device_failure("RS-04", "coord")

    def test_plan_rejects_unknown_condition(self):
        store = self._store_with_slots()
        with self.assertRaises(ValidationError):
            store.create_plan("lead", ["C9-unknown-x"], None, "x")

    def test_condition_trace_explains_unknown_and_records_who_accepted(self):
        store = self._store_with_slots()
        plan = store.create_plan("lead", ["C1-hat-day"], None, "帽檐条件样本不足")
        store.accept_plan(plan.plan_id, ["RS-01"], "体验负责人-王")
        trace = store.condition_trace("C1-hat-day")
        self.assertEqual(trace["plans"][0]["accepted_by"], "体验负责人-王")
        self.assertEqual(trace["plans"][0]["slot_ids"], ["RS-01"])

    def test_reopen_state_replays_journal_check(self):
        store = self._store_with_slots()
        path = store.dir
        plan = store.create_plan("lead", ["C1-hat-day"], None, "x")
        store.accept_plan(plan.plan_id, ["RS-01"], "lead")
        reopened = Store.open(path)
        self.assertEqual(reopened.slot_state("RS-01")["state"], "accepted")
        ok, _ = reopened.verify_journal()
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
