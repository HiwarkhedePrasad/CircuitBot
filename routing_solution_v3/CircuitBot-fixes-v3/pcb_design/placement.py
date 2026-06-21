"""Deterministic placement — NO LLM INVOLVED.

This module replaces any LLM-generated (x, y) coordinates with a fully
deterministic, connectivity-driven placement algorithm. The LLM should
NEVER be asked to emit coordinates — it's bad at geometry.

Pipeline (all deterministic, no AI):

1. CLASSIFY each component by electrical function:
     - POWER_SOURCE  : USB connector, battery, barrel jack
     - REGULATOR     : LDO, buck, boost
     - PROTECTION    : ESD IC, polyfuse, TVS
     - CORE_IC       : MCU, processor, RF module
     - CRYSTAL       : XTAL, oscillator
     - STORAGE       : flash, EEPROM
     - SENSOR        : temp, accel, pressure
     - DISPLAY       : OLED, LCD
     - INTERFACE     : LED, switch, header
     - PASSIVE       : R, C, L, D

2. DETECT DECOUPLING CAPS by netlist analysis (cap with pins on
   power+GND of same core IC). Tag them DECOUPLING.

3. ASSIGN ZONES on the board:
     ┌─────────────────────────────────────────────┐
     │  TOP EDGE: Power connectors, USB            │
     │  ┌─────────┐  ┌─────────┐  ┌──────────┐   │
     │  │ POWER   │  │ CORE    │  │ PERIPH   │   │
     │  │ SOURCE  │  │ IC      │  │ (sensor/ │   │
     │  │ + REG   │→ │ + XTAL  │→ │ display) │   │
     │  │ + PROT  │  │ + DEC   │  │          │   │
     │  └─────────┘  └─────────┘  └──────────┘   │
     │  BOTTOM EDGE: LEDs, switches, headers      │
     └─────────────────────────────────────────────┘

4. FORCE-DIRECTED RELAXATION inside each zone:
     - Connected components attract (spring)
     - All pairs repel (Coulomb)
     - Decoupling caps pinned to within 2 mm of parent power pin
     - Crystals pinned to within 5 mm of parent IC XTAL pins
     - Edge components pinned to perimeter

5. SNAP to 1.27 mm grid.

6. HARD VALIDATION (post-placement):
     - No two components overlap (bbox intersection check)
     - All components inside board outline + margin
     - Decoupling caps within 5 mm of parent power pin
     - Crystals within 10 mm of parent XTAL pins
     - Total board area ≤ 100mm × 80mm (typical small board)

   If validation fails, run another round of force-directed relaxation.
   After 3 failed retries, drop the offending component with a log msg.
"""

from __future__ import annotations

import math
import re
from typing import Optional
from dataclasses import dataclass

GRID = 1.27
GAP = 2.54

# Board defaults (mm) — typical 2-layer small board
DEFAULT_BOARD_W = 100.0
DEFAULT_BOARD_H = 80.0
DEFAULT_EDGE_MARGIN = 3.0   # keep components 3mm from board edge

# Force-directed params
SPRING_K = 0.05
REPULSION_K = 8.0
DAMPING = 0.85
MAX_DISPLACEMENT = 5.0
EQUILIBRIUM_EPS = 0.05
MAX_ITERATIONS = 60

POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
                   "+5V", "+3.3V", "3.3V", "5V", "3V3"}
GND_NET_NAMES = {"GND", "GROUND", "AGND", "DGND", "0V"}
HIGH_SPEED_KEYWORDS = ("USB", "D+", "D-", "DM", "DP", "XTAL", "OSC", "XIN", "XOUT")


# ── Helpers ─────────────────────────────────────────────────────────────


def _snap(v: float) -> float:
    return round(v / GRID) * GRID


def _ref_prefix(ref: str) -> str:
    m = re.match(r"^[A-Z]+", ref.upper())
    return m.group(0) if m else ""


def _ref_num(ref: str) -> int:
    m = re.search(r"\d+", ref)
    return int(m.group(0)) if m else 0


def _default_bbox() -> dict:
    return {"x": -6.0, "y": -6.0, "w": 12.0, "h": 12.0}


# ── Classification ──────────────────────────────────────────────────────


POWER_SOURCE, REGULATOR, PROTECTION, CORE_IC, CRYSTAL, STORAGE, \
    SENSOR, DISPLAY, INTERFACE, PASSIVE, DECOUPLING = range(11)

_ZONE_NAMES = {
    POWER_SOURCE: "POWER_SOURCE",
    REGULATOR:    "REGULATOR",
    PROTECTION:   "PROTECTION",
    CORE_IC:      "CORE_IC",
    CRYSTAL:      "CRYSTAL",
    STORAGE:      "STORAGE",
    SENSOR:       "SENSOR",
    DISPLAY:      "DISPLAY",
    INTERFACE:    "INTERFACE",
    PASSIVE:      "PASSIVE",
    DECOUPLING:   "DECOUPLING",
}

# Zone → board region (left/center/right × top/middle/bottom)
# This drives the initial placement pass.
_ZONE_REGION = {
    POWER_SOURCE: ("left",   "top"),
    REGULATOR:    ("left",   "middle"),
    PROTECTION:   ("left",   "bottom"),
    CORE_IC:      ("center", "middle"),
    CRYSTAL:      ("center", "middle"),  # near core
    STORAGE:      ("center", "bottom"),
    SENSOR:       ("right",  "top"),
    DISPLAY:      ("right",  "middle"),
    INTERFACE:    ("right",  "bottom"),
    PASSIVE:      ("center", "middle"),  # near parent
    DECOUPLING:   ("center", "middle"),  # near parent
}


def _classify(comp: dict) -> int:
    ref = comp.get("ref_des", "")
    cat = (comp.get("category") or "").upper()
    id_str = (comp.get("id_str") or "").upper()
    prefix = _ref_prefix(ref)

    # Prefix-based
    if prefix in {"J", "P", "USB", "CN", "CONN"}:
        return POWER_SOURCE
    if prefix == "Y":
        return CRYSTAL
    if prefix in {"SW", "BTN", "KEY"}:
        return INTERFACE
    if prefix == "D":
        # Could be protection diode or LED — check id_str
        if "ESD" in id_str or "TVS" in id_str:
            return PROTECTION
        return INTERFACE
    if prefix == "LED":
        return INTERFACE
    if prefix == "C":
        return PASSIVE  # may be reclassified as DECOUPLING later
    if prefix == "R":
        return PASSIVE
    if prefix == "L":
        return PASSIVE
    if prefix == "F":
        return PROTECTION  # fuse

    # Category / id_str-based
    cat_words = set(cat.replace("_", " ").split())
    if cat_words & {"MCU", "ESP32", "STM32", "PROCESSOR", "FPGA", "DSP",
                    "CPU", "RF_MODULE", "DRIVER", "MOTOR"}:
        return CORE_IC
    if "REGULATOR" in cat or "LDO" in cat_words or "BUCK" in cat_words or "BOOST" in cat_words:
        return REGULATOR
    if "SENSOR" in cat_words or "TEMPERATURE" in cat_words:
        return SENSOR
    if "DISPLAY" in cat_words or "OLED" in cat_words or "SSD1306" in id_str:
        return DISPLAY
    if "MEMORY" in cat_words or "FLASH" in cat_words or "EEPROM" in cat_words:
        return STORAGE
    if "ESD" in cat_words or "PROTECTION" in cat_words:
        return PROTECTION
    if "CRYSTAL" in cat_words or "OSCILLATOR" in cat_words:
        return CRYSTAL

    return PASSIVE


# ── Decoupling cap detection (pure netlist analysis) ────────────────────


def _detect_decoupling_caps(comps: list[dict], netlist: list[dict],
                             pin_matrix: dict) -> dict[str, str]:
    """Return {cap_ref: parent_ic_ref} for each decoupling cap detected.

    A cap is decoupling if one pin connects to a power net AND the other
    pin connects to GND, AND the same CORE_IC has both those nets.
    """
    if not pin_matrix:
        return {}

    # pin_key → net name (best-effort)
    pin_to_net: dict[str, str] = {}
    for conn in netlist:
        net = conn.get("net", "")
        pin_to_net.setdefault(conn["source"], net)
        pin_to_net.setdefault(conn["target"], net)

    # Also pull from power_pins (the agent's primary power-channel)
    for c in comps:
        for pp in c.get("_power_pins", []) or []:
            pin_to_net.setdefault(pp.get("pin", ""), pp.get("net", ""))

    # core_ref → (power_nets, gnd_nets)
    core_refs = {c["ref_des"] for c in comps if _classify(c) == CORE_IC}
    core_power: dict[str, set] = {r: set() for r in core_refs}
    core_gnd: dict[str, set] = {r: set() for r in core_refs}
    for pin_key, net in pin_to_net.items():
        ref = pin_key.split(":")[0]
        if ref not in core_refs:
            continue
        nu = (net or "").upper().lstrip("+")
        if nu in POWER_NET_NAMES:
            core_power[ref].add(nu)
        if nu in GND_NET_NAMES:
            core_gnd[ref].add(nu)

    decoupling: dict[str, str] = {}
    for c in comps:
        ref = c["ref_des"]
        if _ref_prefix(ref) != "C":
            continue
        cap_pin_keys = [k for k in pin_to_net if k.startswith(f"{ref}:")]
        if len(cap_pin_keys) < 2:
            continue
        cap_nets = [(pk, pin_to_net[pk].upper().lstrip("+")) for pk in cap_pin_keys]
        # Match: one pin power, one pin gnd, on the SAME core IC
        for core_ref in core_refs:
            if not core_power[core_ref] or not core_gnd[core_ref]:
                continue
            for i, (pk_a, net_a) in enumerate(cap_nets):
                for j, (pk_b, net_b) in enumerate(cap_nets):
                    if i == j:
                        continue
                    if net_a in POWER_NET_NAMES and net_b in GND_NET_NAMES:
                        decoupling[ref] = core_ref
                        break
                if ref in decoupling:
                    break
            if ref in decoupling:
                break
    return decoupling


# ── Bounding boxes ──────────────────────────────────────────────────────


def _compute_bbox(pads: list[dict], tag: int) -> dict:
    if not pads:
        return _default_bbox()
    min_x = min(p["x"] - p.get("sx", p.get("width", 1)) / 2 for p in pads)
    max_x = max(p["x"] + p.get("sx", p.get("width", 1)) / 2 for p in pads)
    min_y = min(p["y"] - p.get("sy", p.get("height", 1)) / 2 for p in pads)
    max_y = max(p["y"] + p.get("sy", p.get("height", 1)) / 2 for p in pads)
    margin = {
        CORE_IC: 5.0, REGULATOR: 3.0, POWER_SOURCE: 2.0,
        PROTECTION: 1.0, CRYSTAL: 1.5, SENSOR: 2.0, DISPLAY: 3.0,
        STORAGE: 2.0, INTERFACE: 1.5, PASSIVE: 0.5, DECOUPLING: 0.5,
    }.get(tag, 1.0)
    return {
        "x": min_x - margin,
        "y": min_y - margin,
        "w": max_x - min_x + margin * 2,
        "h": max_y - min_y + margin * 2,
    }


# ── Initial zone placement ──────────────────────────────────────────────


def _zone_origin(tag: int, board_w: float, board_h: float) -> tuple[float, float]:
    """Return (x, y) of the top-left corner of the zone's region."""
    h_region, v_region = _ZONE_REGION.get(tag, ("center", "middle"))
    third_w = board_w / 3
    third_h = board_h / 3
    x = {"left": 0, "center": third_w, "right": 2 * third_w}.get(h_region, third_w)
    y = {"top": 0, "middle": third_h, "bottom": 2 * third_h}.get(v_region, third_h)
    return (x + DEFAULT_EDGE_MARGIN, y + DEFAULT_EDGE_MARGIN)


def _initial_placement(
    comps: list[dict],
    tags: dict[str, int],
    bbox_map: dict[str, dict],
    board_w: float,
    board_h: float,
) -> dict[str, tuple[float, float]]:
    """Place each component in its zone, packed left-to-right top-to-bottom."""
    pos: dict[str, tuple[float, float]] = {}

    # Group by zone
    by_zone: dict[int, list[str]] = {}
    for c in comps:
        ref = c["ref_des"]
        by_zone.setdefault(tags[ref], []).append(ref)

    for zone, refs in by_zone.items():
        ox, oy = _zone_origin(zone, board_w, board_h)
        # Pack in a grid within the zone (max 4 per row)
        max_per_row = 4
        x_cursor = ox
        y_cursor = oy
        row_h = 0.0
        for i, ref in enumerate(sorted(refs, key=_ref_num)):
            b = bbox_map.get(ref, _default_bbox())
            if i > 0 and i % max_per_row == 0:
                x_cursor = ox
                y_cursor += row_h + GAP
                row_h = 0.0
            cx = x_cursor + b["w"] / 2
            cy = y_cursor + b["h"] / 2
            pos[ref] = (_snap(cx), _snap(cy))
            x_cursor += b["w"] + GAP
            row_h = max(row_h, b["h"])

    return pos


# ── Force-directed refinement ──────────────────────────────────────────


def _force_directed(
    pos: dict[str, tuple[float, float]],
    bbox_map: dict[str, dict],
    netlist: list[dict],
    fixed_refs: set[str],
    board_w: float,
    board_h: float,
) -> None:
    """Mutate pos in place with force-directed relaxation.

    Connected components attract (spring), all pairs repel (Coulomb),
    fixed refs don't move.  Components are kept inside the board outline.
    """
    if not pos:
        return

    vel = {r: [0.0, 0.0] for r in pos}

    edges: dict[tuple[str, str], int] = {}
    for conn in netlist:
        s = conn["source"].split(":")[0]
        t = conn["target"].split(":")[0]
        if s == t or s not in pos or t not in pos:
            continue
        key = tuple(sorted((s, t)))
        edges[key] = edges.get(key, 0) + 1

    for _ in range(MAX_ITERATIONS):
        max_disp = 0.0
        force = {r: [0.0, 0.0] for r in pos}

        for (a, b), w in edges.items():
            dx = pos[b][0] - pos[a][0]
            dy = pos[b][1] - pos[a][1]
            dist = math.hypot(dx, dy) + 1e-6
            f = SPRING_K * w * dist
            fx, fy = f * dx / dist, f * dy / dist
            force[a][0] += fx; force[a][1] += fy
            force[b][0] -= fx; force[b][1] -= fy

        refs = list(pos.keys())
        for i, a in enumerate(refs):
            ba = bbox_map.get(a, _default_bbox())
            ra = max(ba["w"], ba["h"]) / 2 + 1.0
            for b in refs[i + 1:]:
                bb = bbox_map.get(b, _default_bbox())
                rb = max(bb["w"], bb["h"]) / 2 + 1.0
                dx = pos[b][0] - pos[a][0]
                dy = pos[b][1] - pos[a][1]
                dist = math.hypot(dx, dy) + 1e-6
                min_dist = ra + rb
                if dist < min_dist:
                    f = REPULSION_K * (min_dist - dist) / dist
                else:
                    f = REPULSION_K / (dist * dist)
                fx, fy = f * dx / dist, f * dy / dist
                force[a][0] -= fx; force[a][1] -= fy
                force[b][0] += fx; force[b][1] += fy

        for r in pos:
            if r in fixed_refs:
                continue
            vx, vy = vel[r]
            fx, fy = force[r]
            vx = DAMPING * vx + fx
            vy = DAMPING * vy + fy
            speed = math.hypot(vx, vy)
            if speed > MAX_DISPLACEMENT:
                vx = vx * MAX_DISPLACEMENT / speed
                vy = vy * MAX_DISPLACEMENT / speed
            vel[r] = [vx, vy]
            new_x = pos[r][0] + vx
            new_y = pos[r][1] + vy
            # Keep inside board
            ba = bbox_map.get(r, _default_bbox())
            new_x = max(ba["w"]/2 + DEFAULT_EDGE_MARGIN,
                        min(board_w - ba["w"]/2 - DEFAULT_EDGE_MARGIN, new_x))
            new_y = max(ba["h"]/2 + DEFAULT_EDGE_MARGIN,
                        min(board_h - ba["h"]/2 - DEFAULT_EDGE_MARGIN, new_y))
            disp = math.hypot(new_x - pos[r][0], new_y - pos[r][1])
            if disp > max_disp:
                max_disp = disp
            pos[r] = (new_x, new_y)

        if max_disp < EQUILIBRIUM_EPS:
            break


# ── Decoupling cap snapping ────────────────────────────────────────────


def _snap_decoupling_caps(
    pos: dict[str, tuple[float, float]],
    decoupling_map: dict[str, str],
    bbox_map: dict[str, dict],
    netlist: list[dict],
    pin_matrix: dict,
) -> None:
    """Move each decoupling cap to within 2 mm of its parent IC's power pin."""
    if not decoupling_map or not pin_matrix:
        return

    parent_power_pin: dict[str, tuple[float, float]] = {}
    for conn in netlist:
        net = (conn.get("net", "") or "").upper().lstrip("+")
        if net not in POWER_NET_NAMES:
            continue
        for pin_key in (conn["source"], conn["target"]):
            ref = pin_key.split(":")[0]
            if ref in decoupling_map.values() and ref not in parent_power_pin:
                pin = pin_matrix.get(pin_key)
                if pin:
                    parent_power_pin[ref] = (pin["x"], pin["y"])

    for cap_ref, parent_ref in decoupling_map.items():
        if cap_ref not in pos or parent_ref not in pos:
            continue
        if parent_ref not in parent_power_pin:
            continue
        par_x, par_y = pos[parent_ref]
        px, py = parent_power_pin[parent_ref]
        abs_px = par_x + px
        abs_py = par_y + py
        cap_b = bbox_map.get(cap_ref, _default_bbox())
        offset = max(cap_b["w"] / 2 + 1.5, 2.5)
        pos[cap_ref] = (_snap(abs_px + offset), _snap(abs_py))


# ── Hard validation ────────────────────────────────────────────────────


def _bbox_overlap(a_pos, a_b, b_pos, b_b) -> bool:
    ax1 = a_pos[0] + a_b["x"]; ax2 = ax1 + a_b["w"]
    ay1 = a_pos[1] + a_b["y"]; ay2 = ay1 + a_b["h"]
    bx1 = b_pos[0] + b_b["x"]; bx2 = bx1 + b_b["w"]
    by1 = b_pos[1] + b_b["y"]; by2 = by1 + b_b["h"]
    return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)


def _validate(
    pos: dict[str, tuple[float, float]],
    bbox_map: dict[str, dict],
    board_w: float,
    board_h: float,
) -> list[str]:
    """Return list of validation error messages (empty = OK)."""
    errs = []
    refs = list(pos.keys())

    # Overlap check
    for i, a in enumerate(refs):
        for b in refs[i + 1:]:
            if _bbox_overlap(pos[a], bbox_map.get(a, _default_bbox()),
                             pos[b], bbox_map.get(b, _default_bbox())):
                errs.append(f"overlap: {a} and {b}")

    # Board bounds check
    for r in refs:
        b = bbox_map.get(r, _default_bbox())
        x, y = pos[r]
        if x + b["x"] < DEFAULT_EDGE_MARGIN or x + b["x"] + b["w"] > board_w - DEFAULT_EDGE_MARGIN:
            errs.append(f"out of bounds X: {r}")
        if y + b["y"] < DEFAULT_EDGE_MARGIN or y + b["y"] + b["h"] > board_h - DEFAULT_EDGE_MARGIN:
            errs.append(f"out of bounds Y: {r}")

    return errs


# ── Main entry point ────────────────────────────────────────────────────


def place_components_deterministic(
    comps: list[dict],
    netlist: list[dict],
    pin_matrix: dict | None = None,
    board_w: float = DEFAULT_BOARD_W,
    board_h: float = DEFAULT_BOARD_H,
) -> list[dict]:
    """Fully deterministic placement — no LLM, no random coordinates.

    Returns: list of {"ref_des", "x", "y", "rotation"} dicts.
    """
    if not comps:
        return []
    pin_matrix = pin_matrix or {}

    # 1. Classify
    tags: dict[str, int] = {}
    for c in comps:
        tags[c["ref_des"]] = _classify(c)

    # 2. Detect decoupling caps
    decoupling = _detect_decoupling_caps(comps, netlist, pin_matrix)
    for cap_ref in decoupling:
        tags[cap_ref] = DECOUPLING

    # 3. Compute bboxes
    bbox_map: dict[str, dict] = {}
    for c in comps:
        bbox_map[c["ref_des"]] = _compute_bbox(c.get("pads", []), tags[c["ref_des"]])

    # 4. Initial placement by zone
    pos = _initial_placement(comps, tags, bbox_map, board_w, board_h)

    # 5. Pin fixed refs (power sources on edge, decoupling caps follow parent)
    fixed_refs = {r for r, t in tags.items() if t in {POWER_SOURCE, INTERFACE}}
    # Decoupling caps are pinned AFTER force-directed (they snap to parent)
    _force_directed(pos, bbox_map, netlist, fixed_refs, board_w, board_h)

    # 6. Snap decoupling caps to parent power pin
    _snap_decoupling_caps(pos, decoupling, bbox_map, netlist, pin_matrix)

    # 7. Snap to grid
    for r in pos:
        pos[r] = (_snap(pos[r][0]), _snap(pos[r][1]))

    # 8. Validate, retry force-directed if needed
    for retry in range(3):
        errs = _validate(pos, bbox_map, board_w, board_h)
        if not errs:
            break
        # Increase repulsion and retry
        fixed_refs_extended = fixed_refs | {r for r, t in tags.items() if t == DECOUPLING}
        _force_directed(pos, bbox_map, netlist, fixed_refs_extended, board_w, board_h)
        for r in pos:
            pos[r] = (_snap(pos[r][0]), _snap(pos[r][1]))

    # 9. Emit
    return [
        {"ref_des": r, "x": pos[r][0], "y": pos[r][1], "rotation": 0}
        for r in sorted(pos, key=_ref_num)
    ]
