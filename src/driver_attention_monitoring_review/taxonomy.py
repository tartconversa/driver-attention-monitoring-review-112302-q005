"""佩戴 × 照明条件分类法及其版本管理。

条件定义更新后，历史复核结论不被删除，而是重新标记适用范围：
- current：当前分类法下仍然适用；
- carried_over：旧分类法结论，经映射后暂时沿用，需要复核确认；
- superseded：旧条件已被新条件取代，结论不再直接适用。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from .models import LIGHTING_VALUES, WEAR_VALUES, ValidationError


@dataclass(frozen=True)
class Taxonomy:
    version: str
    effective_from: str
    description: str
    # condition_id -> (wear 取值集合, lighting 取值集合)
    conditions: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]

    def match(self, wear: str, lighting: str) -> str:
        for cid, (wears, lightings) in self.conditions.items():
            if wear in wears and lighting in lightings:
                return cid
        raise ValidationError(
            f"分类法 {self.version} 无法匹配 wear={wear}, lighting={lighting}"
        )

    def condition_label(self, condition_id: str) -> str:
        wears, lightings = self.conditions[condition_id]
        return f"佩戴[{'/'.join(wears)}] × 照明[{'/'.join(lightings)}]"


def _v1_conditions() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    conditions: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for wear in WEAR_VALUES:
        for lighting in LIGHTING_VALUES:
            conditions[f"C1-{wear}-{lighting}"] = ((wear,), (lighting,))
    return conditions


def _v2_conditions() -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """V2：dusk/night 合并为 low_light；none 改名 no_wear，both 改名 hat_plus_sunglasses。"""
    renamed = {"none": "no_wear", "hat": "hat", "sunglasses": "sunglasses",
               "both": "hat_plus_sunglasses"}
    groups = (("day",), ("dusk", "night"))
    conditions: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {}
    for wear in WEAR_VALUES:
        for group in groups:
            light_name = "day" if group == ("day",) else "low_light"
            cid = f"C2-{renamed[wear]}-{light_name}"
            conditions[cid] = ((wear,), group)
    return conditions


TAXONOMIES: dict[str, Taxonomy] = {
    "v1": Taxonomy(
        version="v1",
        effective_from="2026-10-01T00:00:00Z",
        description="初始分类法：4 种佩戴 × 3 种照明 = 12 个条件，细粒度 dusk/night 分列。",
        conditions=_v1_conditions(),
    ),
    "v2": Taxonomy(
        version="v2",
        effective_from="2026-11-15T00:00:00Z",
        description=(
            "更新分类法：dusk 与 night 合并为 low_light；佩戴命名规范化"
            "（none→no_wear，both→hat_plus_sunglasses），共 8 个条件。"
        ),
        conditions=_v2_conditions(),
    ),
}

DEFAULT_TAXONOMY = "v1"
CURRENT_TAXONOMY = "v2"


def get_taxonomy(version: str) -> Taxonomy:
    try:
        return TAXONOMIES[version]
    except KeyError as exc:
        raise ValidationError(f"未知分类法版本：{version}") from exc


def build_mapping(old: str, new: str) -> dict[str, str]:
    """旧条件 -> 新条件的映射。

    合并型更新（多个旧条件并入同一新条件）是多对一映射：旧结论只能
    carried_over 沿用，不能自动当作新条件的最终结论。
    """
    old_tax = get_taxonomy(old)
    new_tax = get_taxonomy(new)
    mapping: dict[str, str] = {}
    for old_id, (wears, lightings) in old_tax.conditions.items():
        targets = {
            new_tax.match(w, l)
            for w in wears
            for l in lightings
        }
        if len(targets) != 1:
            raise ValidationError(f"旧条件 {old_id} 无法唯一映射到新分类法")
        mapping[old_id] = targets.pop()
    return mapping


def is_many_to_one(old: str, new: str) -> dict[str, bool]:
    """标记每个新条件是否由多个旧条件合并而来。"""
    mapping = build_mapping(old, new)
    fan_in: dict[str, int] = {}
    for new_id in mapping.values():
        fan_in[new_id] = fan_in.get(new_id, 0) + 1
    return {new_id: count > 1 for new_id, count in fan_in.items()}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
