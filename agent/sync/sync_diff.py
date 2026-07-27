"""Compute the diff between an imported schematic and CircuitBot's current state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schematic_importer import ImportedComponent, ImportedLabel, SchematicImport


@dataclass
class ComponentDiff:
    """Change to a single component's properties."""
    ref: str
    changes: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # field -> (old, new)


@dataclass
class LabelUpdate:
    """Change to a label's net assignment."""
    ref: str
    pin: str
    old_net: str
    new_net: str


@dataclass
class SyncDiff:
    """Computed diff between imported schematic and current state."""
    components_to_add: list[ImportedComponent] = field(default_factory=list)
    components_to_remove: list[str] = field(default_factory=list)
    components_to_modify: list[ComponentDiff] = field(default_factory=list)
    labels_to_add: list[ImportedLabel] = field(default_factory=list)
    labels_to_remove: list[ImportedLabel] = field(default_factory=list)
    labels_to_update: list[LabelUpdate] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.components_to_add
            and not self.components_to_remove
            and not self.components_to_modify
            and not self.labels_to_add
            and not self.labels_to_remove
            and not self.labels_to_update
        )

    def summary(self) -> dict[str, int]:
        return {
            "components_added": len(self.components_to_add),
            "components_removed": len(self.components_to_remove),
            "components_modified": len(self.components_to_modify),
            "labels_added": len(self.labels_to_add),
            "labels_removed": len(self.labels_to_remove),
            "labels_updated": len(self.labels_to_update),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "components_to_add": [
                {"ref": c.ref, "lib_id": c.lib_id, "value": c.value}
                for c in self.components_to_add
            ],
            "components_to_remove": self.components_to_remove,
            "components_to_modify": [
                {"ref": d.ref, "changes": {k: {"old": v[0], "new": v[1]} for k, v in d.changes.items()}}
                for d in self.components_to_modify
            ],
            "labels_added": [
                {"text": l.text, "type": l.label_type}
                for l in self.labels_to_add
            ],
            "labels_removed": [
                {"text": l.text, "type": l.label_type}
                for l in self.labels_to_remove
            ],
            "labels_updated": [
                {"ref": u.ref, "pin": u.pin, "old": u.old_net, "new": u.new_net}
                for u in self.labels_to_update
            ],
            "summary": self.summary(),
        }


def compute_diff(
    imported: SchematicImport,
    current_components: list[dict],
    current_labels: list[dict] | None = None,
    matched_refs: dict[str, str] | None = None,
) -> SyncDiff:
    """Compute the diff between imported schematic and current state.

    Args:
        imported: Parsed schematic data.
        current_components: List of ComponentSelection dicts from AgentState.
        current_labels: List of label dicts from AgentState (optional).
        matched_refs: Dict mapping circuit_id -> kicad_ref from matching (optional).

    Returns:
        SyncDiff describing what needs to change.
    """
    diff = SyncDiff()

    # Build lookup of current components by ref
    current_by_ref: dict[str, dict] = {}
    for comp in current_components:
        ref = comp.get("ref_des", "")
        if ref:
            current_by_ref[ref] = comp

    # Build lookup of imported components by ref
    imported_by_ref: dict[str, ImportedComponent] = {}
    for comp in imported.components:
        imported_by_ref[comp.ref] = comp

    matched_kicad_refs = set()
    if matched_refs:
        matched_kicad_refs = set(matched_refs.values())

    # 1. Components to ADD: in imported but not in current state
    for imp_ref, imp_comp in imported_by_ref.items():
        if imp_ref not in current_by_ref:
            diff.components_to_add.append(imp_comp)

    # 2. Components to REMOVE: in current state but not in imported
    for cur_ref in current_by_ref:
        if cur_ref not in imported_by_ref:
            diff.components_to_remove.append(cur_ref)

    # 3. Components to MODIFY: matched but properties changed
    for imp_ref, imp_comp in imported_by_ref.items():
        if imp_ref in current_by_ref:
            cur_comp = current_by_ref[imp_ref]
            changes: dict[str, tuple[Any, Any]] = {}

            # Compare value
            cur_value = cur_comp.get("value", "")
            if imp_comp.value and cur_value and imp_comp.value != cur_value:
                changes["value"] = (cur_value, imp_comp.value)

            # Compare footprint
            cur_fp = cur_comp.get("footprint", "")
            if imp_comp.footprint and cur_fp and imp_comp.footprint != cur_fp:
                changes["footprint"] = (cur_fp, imp_comp.footprint)

            # Compare lib_id (symbol)
            cur_symbol = cur_comp.get("id_str", "")
            if imp_comp.lib_id and cur_symbol and imp_comp.lib_id != cur_symbol:
                changes["id_str"] = (cur_symbol, imp_comp.lib_id)

            if changes:
                diff.components_to_modify.append(ComponentDiff(ref=imp_ref, changes=changes))

    # 4. Label diffing (basic — compare imported labels against current labels)
    if current_labels is not None:
        # Build set of current label texts
        current_label_texts = {l.get("text", l.get("net", "")) for l in current_labels if l.get("text") or l.get("net")}

        for imp_label in imported.labels:
            if imp_label.text not in current_label_texts:
                diff.labels_to_add.append(imp_label)

    return diff
