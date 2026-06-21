"""Patch for agent/utils.py — _extract_pins_from_ops()

Drop-in replacement. The only change is that the pin's `angle` (KiCad
degrees: 0=right, 90=up, 180=left, 270=down) is stored in the pin_matrix
entry. Both the Python schematic router (agent/layout_engine.py) and the
PCB router (pcb_design/router2.py) read this to know which direction a
wire must exit the pin.

Replace the existing _extract_pins_from_ops function in
/home/z/my-project/CircuitBot/agent/utils.py with the function below.
"""

# ── Drop-in replacement begins ──────────────────────────────────────────


def _extract_pins_from_ops(ops: list, ref_des: str) -> dict:
    GRID_SIZE = 1.27
    pin_matrix = {}
    for op in ops:
        if op[0] != "pin":
            continue
        at = _get_attr(op, "at")
        len_node = _get_attr(op, "length")
        num_node = _get_attr(op, "number")
        if not at or not len_node or not num_node:
            continue
        try:
            px = float(at[1])
            py = float(at[2])
            ang_deg = float(at[3]) if len(at) > 3 else 0
            length = float(len_node[1])
        except (ValueError, IndexError):
            continue
        ang_rad = ang_deg * 3.14159 / 180.0
        cos_a = round(1.0 if ang_deg == 0 else (-1.0 if ang_deg == 180 else 0.0), 2)
        sin_a = round(1.0 if ang_deg == 90 else (-1.0 if ang_deg == 270 else 0.0), 2)
        if abs(cos_a) < 0.1 and abs(sin_a) < 0.1:
            import math
            cos_a = math.cos(ang_rad)
            sin_a = math.sin(ang_rad)
        ex = px + cos_a * length
        ey = py + sin_a * length
        name_node = _get_attr(op, "name")
        pin_name = name_node[1] if name_node else ""
        pin_num = num_node[1].replace('"', '').strip()
        if not pin_num:
            continue
        # etype (electrical type) — used by netlist generation
        etype_node = _get_attr(op, "electrical_type")
        etype = etype_node[1] if etype_node else "passive"
        key = f"{ref_des}:{pin_num}"
        if key in pin_matrix:
            continue
        pin_matrix[key] = {
            "x": round(ex / GRID_SIZE) * GRID_SIZE,
            "y": round(ey / GRID_SIZE) * GRID_SIZE,
            "name": pin_name.strip(),
            "ref_des": ref_des,
            "pin_num": pin_num,
            # KiCad angle convention: 0=right, 90=up, 180=left, 270=down.
            # Routers use this to know which way to exit the symbol body.
            "angle": int(round(ang_deg)) % 360,
            "etype": etype,
        }
    return pin_matrix


# ── Drop-in replacement ends ────────────────────────────────────────────
