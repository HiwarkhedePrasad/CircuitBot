"""Power and signal label placement.

Power labels (VCC, 3V3, GND) are placed at component pins connected
to power or ground nets.  Signal labels are placed for major nets
that benefit from being named on the schematic.
"""

from __future__ import annotations

from typing import Any


def _pin_sheet_position(ctx: Any, pin_key: str) -> tuple[float, float]:
    """Calculate the absolute sheet position of a pin."""
    if ":" not in pin_key:
        return (0.0, 0.0)
    ref = pin_key.split(":")[0]
    comp_positions = ctx.metadata.get("component_positions", {})
    comp_pos = comp_positions.get(ref)
    if comp_pos is None:
        return (0.0, 0.0)

    cx, cy, _rot = comp_pos
    graph = ctx.synthesis_graph
    if graph is None:
        return (cx, cy)

    comp = graph.components.get(ref)
    if comp is None:
        return (cx, cy)

    pin = comp.pins.get(pin_key)
    if pin is None:
        return (cx, cy)

    px, py = getattr(pin, "position", (0.0, 0.0))
    return (cx + px, cy + py)


def _net_name_to_label(net_name: str) -> str:
    """Convert a net name to a schematic label string."""
    if net_name.upper() in ("GND", "VSS", "VEE", "AGND", "DGND", "PGND", "EPAD", "SHIELD"):
        return "GND"
    if net_name.upper() in ("VCC", "VDD"):
        return "VCC"
    if net_name.upper() in ("3V3", "3.3V"):
        return "+3V3"
    if net_name.upper() in ("5V", "5V0"):
        return "+5V"
    if net_name.upper() in ("VIN",):
        return "VIN"
    if net_name.upper() in ("VOUT",):
        return "VOUT"
    return net_name


def place_labels(ctx: Any) -> list[dict]:
    """Place power and signal labels on the schematic.

    Returns list of label dicts compatible with AgentState.power_labels.
    Stores them in ctx.metadata["labels"].
    """
    graph = ctx.synthesis_graph
    if graph is None:
        return []

    labels: list[dict] = []
    seen_labels: set[str] = set()

    for net in graph.nets.values():
        net_name = net.name
        if not net_name or not net.pins:
            continue

        role = getattr(net.role, "value", "") if hasattr(net.role, "value") else ""
        if role not in ("power", "ground", "signal"):
            continue

        label_text = _net_name_to_label(net_name)
        if label_text in seen_labels:
            continue

        if role in ("power", "ground"):
            # Place label at first pin position
            first_pin = next(iter(net.pins))
            x, y = _pin_sheet_position(ctx, first_pin)
            labels.append({
                "net": net_name,
                "label": label_text,
                "orientation": "horizontal",
                "x": round(x, 2),
                "y": round(y - 5.0, 2),
                "font_size": 1.27,
            })
            seen_labels.add(label_text)

        elif role == "signal":
            # Only label signal nets with meaningful names (not auto-generated)
            if not net_name.upper().startswith("N$") and not net_name.startswith("_"):
                first_pin = next(iter(net.pins))
                x, y = _pin_sheet_position(ctx, first_pin)
                labels.append({
                    "net": net_name,
                    "label": label_text,
                    "orientation": "horizontal",
                    "x": round(x, 2),
                    "y": round(y, 2),
                    "font_size": 1.0,
                })
                seen_labels.add(label_text)

    ctx.metadata["labels"] = labels
    return labels
