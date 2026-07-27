"""Live schematic context for the canvas-aware copilot.

Builds a compact, LLM-ready text summary of the current schematic canvas
from DesignSession state. Used by the modify pipeline, design queries, and
every copilot request that needs to understand the canvas.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.state import DesignSession


def build_canvas_context(ds: "DesignSession") -> str:
    """Build a compact text summary of the current schematic canvas.

    Includes:
    - Revision number
    - Components with ref, type, value, position
    - Nets with connected pins
    - Unconnected pins
    - Net labels
    - Currently selected objects (if any)

    This context is injected into every copilot request so the LLM
    has an accurate representation of the canvas.
    """
    design = ds.get_design()
    components = design.get("selected_components", [])
    wire_paths = design.get("wire_paths", [])
    net_labels = design.get("net_labels", [])
    pin_matrix = design.get("pin_matrix", {})
    nets = design.get("nets", {})
    revision = ds.revision

    lines = [f"Canvas revision: {revision}"]

    # ── Components ──────────────────────────────────────────────────────────
    if components:
        lines.append(f"\nComponents ({len(components)}):")
        for c in components:
            ref = c.get("ref_des") or c.get("ref") or "?"
            name = c.get("id_str") or c.get("name") or "?"
            value = c.get("value", "")
            x = c.get("x", 0)
            y = c.get("y", 0)
            footprint = c.get("footprint", "")
            line = f"  {ref}: {name}"
            if value:
                line += f", {value}"
            if footprint:
                line += f", {footprint}"
            line += f" at ({x:.2f}, {y:.2f})"
            lines.append(line)

        # Component pin details
        lines.append("\nComponent pins:")
        for c in components:
            ref = c.get("ref_des") or c.get("ref") or "?"
            pins = c.get("pins", [])
            if pins:
                pin_strs = []
                for p in pins:
                    pin_num = p.get("number") or p.get("name", "?")
                    pin_strs.append(str(pin_num))
                lines.append(f"  {ref}: {', '.join(pin_strs)}")
    else:
        lines.append("\nNo components on canvas.")

    # ── Nets ────────────────────────────────────────────────────────────────
    if nets:
        lines.append(f"\nNets ({len(nets)}):")
        for net_name, pins in sorted(nets.items()):
            if isinstance(pins, list) and len(pins) > 0:
                if isinstance(pins[0], (list, tuple)):
                    pin_strs = [f"{ref}:{pin}" for ref, pin in pins]
                else:
                    pin_strs = [str(p) for p in pins]
                lines.append(f"  {net_name}: {', '.join(pin_strs)}")
    elif wire_paths:
        # Nets not yet computed, but wires exist — show wire connections
        lines.append("\nWire connections:")
        for w in wire_paths:
            src = w.get("source", "?")
            tgt = w.get("target", "?")
            net = w.get("net", "")
            net_str = f" (net: {net})" if net else ""
            lines.append(f"  {src} -- {tgt}{net_str}")

    # ── Unconnected pins ────────────────────────────────────────────────────
    connected = set()
    for net_name, pins in (nets or {}).items():
        if isinstance(pins, list):
            for pin_entry in pins:
                if isinstance(pin_entry, (list, tuple)) and len(pin_entry) == 2:
                    connected.add(f"{pin_entry[0]}:{pin_entry[1]}")
                elif isinstance(pin_entry, str):
                    connected.add(pin_entry)

    # Also mark pins connected via wires
    for w in wire_paths:
        if w.get("source"):
            connected.add(w["source"])
        if w.get("target"):
            connected.add(w["target"])

    unconnected = []
    for comp in components:
        ref = comp.get("ref_des") or comp.get("ref", "?")
        pins = comp.get("pins", [])
        for pin in pins:
            pin_num = pin.get("number") or pin.get("name", "?")
            pin_key = f"{ref}:{pin_num}"
            if pin_key not in connected:
                unconnected.append(pin_key)

    if unconnected:
        lines.append(f"\nUnconnected pins ({len(unconnected)}):")
        lines.append(f"  {', '.join(unconnected)}")

    # ── Net labels ──────────────────────────────────────────────────────────
    if net_labels:
        lines.append(f"\nNet labels ({len(net_labels)}):")
        for nl in net_labels:
            net = nl.get("net", "?")
            x = nl.get("x", 0)
            y = nl.get("y", 0)
            pin = nl.get("pin", "")
            pin_str = f" on {pin}" if pin else ""
            lines.append(f"  {net} at ({x:.2f}, {y:.2f}){pin_str}")

    # ── Selection ───────────────────────────────────────────────────────────
    selected = design.get("selected_components", [])
    if selected and len(selected) < len(components):
        sel_refs = [c.get("ref_des") or c.get("ref", "?") for c in selected]
        lines.append(f"\nSelected: {', '.join(sel_refs)}")

    return "\n".join(lines)


def build_canvas_context_for_modify(ds: "DesignSession", prompt: str) -> str:
    """Build canvas context specifically for the modify pipeline.

    Adds the modification prompt and highlights what the user might
    be referring to.
    """
    context = build_canvas_context(ds)
    return f"""Current schematic state:
{context}

User modification request: {prompt}

Based on the schematic above, identify what the user wants to modify.
Use the reference designators and component names shown above.
If the user refers to a component by description (e.g., "the resistor"),
match it to the appropriate component in the list."""


def build_canvas_context_for_query(ds: "DesignSession", question: str) -> str:
    """Build canvas context for a design query (read-only question)."""
    context = build_canvas_context(ds)
    return f"""Current schematic state:
{context}

Question: {question}

Answer the question based ONLY on the schematic state shown above.
If the information is not available in the schematic, say so clearly.
Be precise about component reference designators, pin numbers, and net names."""
