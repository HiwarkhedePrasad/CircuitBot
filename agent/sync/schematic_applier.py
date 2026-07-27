"""Apply sync diff to CircuitBot's internal state.

Converts imported KiCad data into CircuitBot's AgentState-compatible format
and applies diffs computed by sync_diff.
"""

from __future__ import annotations

from typing import Any

from .schematic_importer import (
    ImportedComponent,
    ImportedLabel,
    ImportedPowerSymbol,
    SchematicImport,
)
from .sync_diff import SyncDiff


def imported_to_component_selection(comp: ImportedComponent) -> dict:
    """Convert an ImportedComponent to a ComponentSelection-compatible dict."""
    return {
        "id_str": comp.lib_id,
        "ref_des": comp.ref,
        "category": _category_from_lib_id(comp.lib_id),
        "description": comp.value,
        "footprint": comp.footprint,
        "pads": [],  # Will be populated by dispatch node if needed
        "justification": f"imported from KiCad ({comp.lib_id})",
        "datasheet_text": "",
    }


def _category_from_lib_id(lib_id: str) -> str:
    """Infer component category from KiCad library ID."""
    lib_lower = lib_id.lower()
    if "device:r" in lib_lower or "resistor" in lib_lower:
        return "RESISTOR"
    if "device:c" in lib_lower or "capacitor" in lib_lower:
        return "CAPACITOR"
    if "device:l" in lib_lower or "inductor" in lib_lower:
        return "INDUCTOR"
    if "device:d" in lib_lower or "led" in lib_lower:
        return "DIODE"
    if "device:q" in lib_lower or "transistor" in lib_lower:
        return "TRANSISTOR"
    if "regulator" in lib_lower or "ldo" in lib_lower:
        return "REGULATOR"
    if "connector" in lib_lower:
        return "CONNECTOR"
    if "crystal" in lib_lower or "oscillator" in lib_lower:
        return "CRYSTAL"
    if "device:usb" in lib_lower or "usb" in lib_lower:
        return "USB"
    return "IC"


def imported_to_placement(comp: ImportedComponent) -> dict:
    """Convert an ImportedComponent to a ComponentPlacement-compatible dict."""
    return {
        "ref_des": comp.ref,
        "x": comp.x,
        "y": comp.y,
    }


def imported_to_power_label(ps: ImportedPowerSymbol) -> dict:
    """Convert an ImportedPowerSymbol to a power_label dict."""
    return {
        "net": ps.value,
        "x": ps.x,
        "y": ps.y,
        "dir": _rotation_to_dir(ps.rotation),
    }


def _rotation_to_dir(rotation: float) -> str:
    """Convert rotation angle to direction string."""
    r = rotation % 360
    if r < 45 or r >= 315:
        return "up"
    if 45 <= r < 135:
        return "right"
    if 135 <= r < 225:
        return "down"
    return "left"


def imported_to_label_dict(label: ImportedLabel) -> dict:
    """Convert an ImportedLabel to a net_label dict."""
    return {
        "text": label.text,
        "type": label.label_type,
        "at": {"x": label.x, "y": label.y},
        "rotation": label.rotation,
    }


def apply_import(
    imported: SchematicImport,
    design: dict,
) -> dict:
    """Apply a full schematic import to the design dict.

    This replaces the design's component list, placements, and labels
    with data from the imported schematic.

    Args:
        imported: Parsed schematic data.
        design: The design dict to update (modified in-place).

    Returns:
        Summary dict with counts of what was imported.
    """
    # Convert components
    design["selected_components"] = [
        imported_to_component_selection(c) for c in imported.components
    ]

    # Convert placements
    design["component_placements"] = [
        imported_to_placement(c) for c in imported.components
    ]

    # Convert power labels
    design["power_labels"] = [
        imported_to_power_label(ps) for ps in imported.power_symbols
    ]

    # Convert net labels
    design["net_labels"] = [
        imported_to_label_dict(l) for l in imported.labels
    ]

    # Convert wires to wire_paths (simplified — each wire becomes a path)
    wire_paths = []
    for wire in imported.wires:
        wire_paths.append({
            "source": f"({wire.x1},{wire.y1})",
            "target": f"({wire.x2},{wire.y2})",
            "path": [
                {"x": wire.x1, "y": wire.y1},
                {"x": wire.x2, "y": wire.y2},
            ],
        })
    design["wire_paths"] = wire_paths

    # Populate component_ops from imported lib_symbols so that
    # generate_kicad_sch() can build the lib_symbols section on re-export.
    # Each component's id_str (e.g. "Device:R") maps to its parsed S-expression ops.
    comp_ops: dict[str, list] = {}
    for comp in imported.components:
        lib_id = comp.lib_id
        if lib_id and lib_id not in comp_ops and lib_id in imported.lib_symbols:
            comp_ops[lib_id] = imported.lib_symbols[lib_id]
    design["component_ops"] = comp_ops

    return {
        "components": len(imported.components),
        "power_symbols": len(imported.power_symbols),
        "wires": len(imported.wires),
        "labels": len(imported.labels),
        "no_connects": len(imported.no_connects),
        "junctions": len(imported.junctions),
        "sheets": len(imported.sheets),
    }


def apply_diff(
    diff: SyncDiff,
    design: dict,
) -> dict:
    """Apply a sync diff to the design dict.

    Args:
        diff: The computed diff to apply.
        design: The design dict to update (modified in-place).

    Returns:
        Summary dict with counts of changes applied.
    """
    applied = {
        "components_added": 0,
        "components_removed": 0,
        "components_modified": 0,
    }

    # Get current components
    components = design.get("selected_components", [])
    placements = design.get("component_placements", [])

    # Apply component additions
    for imp_comp in diff.components_to_add:
        new_comp = imported_to_component_selection(imp_comp)
        components.append(new_comp)
        placements.append(imported_to_placement(imp_comp))
        applied["components_added"] += 1

    # Apply component removals
    if diff.components_to_remove:
        refs_to_remove = set(diff.components_to_remove)
        components = [c for c in components if c.get("ref_des") not in refs_to_remove]
        placements = [p for p in placements if p.get("ref_des") not in refs_to_remove]
        applied["components_removed"] = len(refs_to_remove)

    # Apply component modifications
    comp_by_ref = {c.get("ref_des", ""): c for c in components}
    for comp_mod in diff.components_to_modify:
        if comp_mod.ref in comp_by_ref:
            comp = comp_by_ref[comp_mod.ref]
            for field_name, (old_val, new_val) in comp_mod.changes.items():
                comp[field_name] = new_val
            applied["components_modified"] += 1

    design["selected_components"] = components
    design["component_placements"] = placements

    return applied
