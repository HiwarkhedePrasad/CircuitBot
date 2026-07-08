"""Export adapter — converts LayoutContext to AgentState format.

Produces the component_placements, wire_paths, and power_labels dicts
expected by kicad_export.py and the frontend.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import SchematicOutput, WireSegment


def _placement_to_dict(ref: str, x: float, y: float) -> dict:
    return {"ref_des": ref, "x": round(x, 2), "y": round(y, 2)}


def _wire_to_dict(w: WireSegment) -> dict:
    path = [{"x": round(px, 2), "y": round(py, 2), "angle": "-"}
            for px, py in w.points]
    return {"source": w.source, "target": w.target, "path": path}


def _label_to_dict(label: dict) -> dict:
    return {
        "net": label.get("net", ""),
        "label": label.get("label", ""),
        "orientation": label.get("orientation", "horizontal"),
        "x": label.get("x", 0.0),
        "y": label.get("y", 0.0),
        "font_size": label.get("font_size", 1.0),
    }


def export(ctx: Any) -> SchematicOutput:
    """Convert the context's internal data to SchematicOutput.

    Reads from ctx.metadata and ctx.wires.  Produces the dict format
    expected by the KiCad exporter and frontend.
    """
    comp_positions = ctx.metadata.get("component_positions", {})

    component_placements = [
        _placement_to_dict(ref, x, y)
        for ref, (x, y, _rot) in comp_positions.items()
    ]

    # All wires: intra-block + inter-block
    all_wires = ctx.wires or []
    wire_paths = [_wire_to_dict(w) for w in all_wires]

    # Labels
    raw_labels = ctx.metadata.get("labels", [])
    power_labels = [_label_to_dict(lb) for lb in raw_labels]

    output = SchematicOutput(
        component_placements=component_placements,
        wire_paths=wire_paths,
        power_labels=power_labels,
    )

    ctx.output = output
    return output
