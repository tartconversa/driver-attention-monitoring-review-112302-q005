"""命令行入口：python -m driver_attention_monitoring_review ..."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .analysis import run_analysis
from .importer import ValidationError
from .metrics import EvidenceRules
from .models import BusinessRuleError, Decision
from .report import (
    full_report,
    slots_report,
    snapshot_listing,
    trace_report,
)
from .store import Store
from .synthdata import generate
from .taxonomy import TAXONOMIES, get_taxonomy, now_utc
from .watch import import_once, watch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="driver_attention_monitoring_review",
        description="驾驶员注意力监测分层体验复核（仅处理本地合成资料）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="创建状态库")
    p.add_argument("state_dir")

    p = sub.add_parser("import", help="单次幂等导入 CSV 资料")
    p.add_argument("state_dir")
    p.add_argument("data_dir")
    p.add_argument("--actor", default="ingest")

    p = sub.add_parser("watch", help="轮询目录并持续导入")
    p.add_argument("state_dir")
    p.add_argument("data_dir")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--cycles", type=int, default=None)
    p.add_argument("--actor", default="ingest")

    p = sub.add_parser("generate-synth", help="生成固定种子的合成 CSV 资料")
    p.add_argument("output_dir")
    p.add_argument("--seed", type=int, default=20261124)

    p = sub.add_parser("analyze", help="计算分层指标、生成证据快照并输出中文报告")
    p.add_argument("state_dir")
    p.add_argument("--report", help="把 Markdown 报告写入该路径")
    p.add_argument("--json", dest="json_out", help="把结构化结果写入该 JSON 路径")
    p.add_argument("--min-sessions", type=int, default=8)
    p.add_argument("--min-drivers", type=int, default=4)

    p = sub.add_parser("report", help="输出当前证据快照清单与名额状态")
    p.add_argument("state_dir")
    p.add_argument("--out", help="写入 Markdown 文件，否则打印到标准输出")

    p = sub.add_parser("taxonomy", help="分类法版本管理")
    ts = p.add_subparsers(dest="subcommand", required=True)
    ts.add_parser("list", help="列出可用分类法")
    ta = ts.add_parser("activate", help="启用新分类法并重标历史结论适用范围")
    ta.add_argument("state_dir")
    ta.add_argument("version")
    ta.add_argument("--actor", required=True)
    ta.add_argument("--note", default="")

    p = sub.add_parser("decision", help="体验负责人决策")
    ds = p.add_subparsers(dest="subcommand", required=True)
    da = ds.add_parser("add", help="登记决策（继续观察/调整产品说明/申请研究）")
    da.add_argument("state_dir")
    da.add_argument("--actor", required=True)
    da.add_argument("--condition", required=True)
    da.add_argument("--snapshot", required=True)
    da.add_argument(
        "--action", required=True,
        choices=["continue_monitoring", "adjust_copy", "request_research"],
    )
    da.add_argument("--note", default="")

    p = sub.add_parser("plan", help="研究计划")
    ps = p.add_subparsers(dest="subcommand", required=True)
    pc = ps.add_parser("create", help="创建研究计划（占用名额前不消耗名额）")
    pc.add_argument("state_dir")
    pc.add_argument("--created-by", required=True)
    pc.add_argument("--conditions", required=True, help="逗号分隔的条件标识")
    pc.add_argument("--region", default=None)
    pc.add_argument("--note", default="")
    pa = ps.add_parser("accept", help="接受计划并占用 open 名额")
    pa.add_argument("state_dir")
    pa.add_argument("plan_id")
    pa.add_argument("--slots", required=True, help="逗号分隔的 slot_id")
    pa.add_argument("--accepted-by", required=True)

    p = sub.add_parser("slot", help="名额运营")
    ss = p.add_subparsers(dest="subcommand", required=True)
    se = ss.add_parser("execute", help="执行名额并登记采集资料")
    se.add_argument("state_dir")
    se.add_argument("slot_id")
    se.add_argument("--consent-scope", required=True,
                    choices=["retention-ok", "research-only"])
    se.add_argument("--actor", required=True)
    se.add_argument("--detail", default="")
    sw = ss.add_parser("withdraw", help="撤回同意（释放未执行部分/处置已采集资料）")
    sw.add_argument("state_dir")
    sw.add_argument("slot_id")
    sw.add_argument("--actor", required=True)
    sf = ss.add_parser("device-fail", help="设备失效（仅释放未执行名额）")
    sf.add_argument("state_dir")
    sf.add_argument("slot_id")
    sf.add_argument("--actor", required=True)

    p = sub.add_parser("material", help="已采集资料处置")
    ms = p.add_subparsers(dest="subcommand", required=True)
    mr = ms.add_parser("remove", help="移除处于 removal_pending 的资料")
    mr.add_argument("state_dir")
    mr.add_argument("material_id")
    mr.add_argument("--actor", required=True)

    p = sub.add_parser("trace", help="追溯某条件为何未知、决策与研究安排")
    p.add_argument("state_dir")
    p.add_argument("condition_id")

    p = sub.add_parser("verify", help="校验审计哈希链")
    p.add_argument("state_dir")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _dispatch(args)
    except (ValidationError, BusinessRuleError, FileExistsError,
            FileNotFoundError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    cmd = args.command

    if cmd == "init":
        Store.create(args.state_dir)
        print(f"已创建状态库：{args.state_dir}")
        return 0

    if cmd == "generate-synth":
        counts = generate(args.output_dir, seed=args.seed)
        print("已生成合成资料：")
        for name, count in counts.items():
            print(f"  {name}: {count} 行")
        return 0

    if cmd == "import":
        store = Store.open(args.state_dir)
        print(json.dumps(import_once(store, args.data_dir, args.actor),
                         ensure_ascii=False, indent=2))
        return 0

    if cmd == "watch":
        store = Store.open(args.state_dir)
        log = watch(store, args.data_dir, poll_interval=args.interval,
                    max_cycles=args.cycles, actor=args.actor)
        print(json.dumps(log, ensure_ascii=False, indent=2))
        return 0

    if cmd == "taxonomy" and args.subcommand == "list":
        for version, tax in TAXONOMIES.items():
            print(f"{version}（生效 {tax.effective_from}）：{tax.description}")
        return 0

    if cmd == "taxonomy" and args.subcommand == "activate":
        store = Store.open(args.state_dir)
        result = store.activate_taxonomy(args.version, args.actor, args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if cmd == "analyze":
        store = Store.open(args.state_dir)
        rules = EvidenceRules(
            min_sessions=args.min_sessions,
            min_distinct_drivers=args.min_drivers,
        )
        outcome = run_analysis(store, rules=rules)
        taxonomy = get_taxonomy(store.state["active_taxonomy"])
        text = full_report(taxonomy, outcome["results"])
        if args.report:
            Path(args.report).write_text(text, encoding="utf-8")
            print(f"报告已写入 {args.report}")
        else:
            print(text)
        if args.json_out:
            payload = {
                cid: {
                    "classification": r["classification"],
                    "reasons": r["reasons"],
                    "suggested_action": r["suggested_action"],
                    "metrics": r["metrics"].to_dict(),
                }
                for cid, r in outcome["results"].items()
            }
            Path(args.json_out).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"结构化结果已写入 {args.json_out}")
        print(f"\n变化条件：{outcome['changed_conditions'] or '无'}")
        print(f"受影响决策：{outcome['affected_decisions'] or '无'}")
        return 0

    if cmd == "report":
        store = Store.open(args.state_dir)
        parts = [
            snapshot_listing(store.snapshots()),
            "",
            slots_report([store.slot_state(sid) for sid in
                          sorted(store.state["slots"])]),
        ]
        text = "\n".join(parts)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"报告已写入 {args.out}")
        else:
            print(text)
        return 0

    if cmd == "decision" and args.subcommand == "add":
        store = Store.open(args.state_dir)
        decision = Decision(
            decision_id=store._next_id("DEC-"),
            ts=now_utc(),
            actor=args.actor,
            condition_id=args.condition,
            taxonomy_version=store.state["active_taxonomy"],
            action=args.action,
            snapshot_id=args.snapshot,
            note=args.note,
        )
        store.add_decision(decision)
        print(f"已登记决策 {decision.decision_id}")
        return 0

    if cmd == "plan" and args.subcommand == "create":
        store = Store.open(args.state_dir)
        plan = store.create_plan(
            args.created_by,
            [c.strip() for c in args.conditions.split(",") if c.strip()],
            args.region,
            args.note,
        )
        print(f"已创建研究计划 {plan.plan_id}（状态 proposed，尚未占用名额）")
        return 0

    if cmd == "plan" and args.subcommand == "accept":
        store = Store.open(args.state_dir)
        slot_ids = [s.strip() for s in args.slots.split(",") if s.strip()]
        plan = store.accept_plan(args.plan_id, slot_ids, args.accepted_by)
        print(f"计划 {plan.plan_id} 已接受，占用名额 {plan.slot_ids}")
        return 0

    if cmd == "slot" and args.subcommand == "execute":
        store = Store.open(args.state_dir)
        material = store.execute_slot(
            args.slot_id, args.consent_scope, args.actor, args.detail
        )
        print(f"已执行 {args.slot_id}，采集资料 {material.material_id}")
        return 0

    if cmd == "slot" and args.subcommand == "withdraw":
        store = Store.open(args.state_dir)
        print(json.dumps(
            store.withdraw_consent(args.slot_id, args.actor),
            ensure_ascii=False, indent=2,
        ))
        return 0

    if cmd == "slot" and args.subcommand == "device-fail":
        store = Store.open(args.state_dir)
        print(json.dumps(
            store.device_failure(args.slot_id, args.actor),
            ensure_ascii=False, indent=2,
        ))
        return 0

    if cmd == "material" and args.subcommand == "remove":
        store = Store.open(args.state_dir)
        material = store.remove_material(args.material_id, args.actor)
        print(f"资料 {material.material_id} 已移除")
        return 0

    if cmd == "trace":
        store = Store.open(args.state_dir)
        print(trace_report(store.condition_trace(args.condition_id)))
        return 0

    if cmd == "verify":
        store = Store.open(args.state_dir)
        ok, msg = store.verify_journal()
        print(("通过：" if ok else "失败：") + msg)
        return 0 if ok else 1

    raise AssertionError(f"未实现的命令：{cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
