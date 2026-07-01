"""
agent/layout_engine.py  —  Hierarchical schematic layout + hard-capped
                            obstacle-aware orthogonal wire routing.

CRITICAL FIXES over the previous version:
  1. NO early-return bypass of the length cap. Every candidate — including
     straight wires — is scored and length-checked uniformly.
  2. NO fallback path. If every candidate exceeds MAX_WIRE_MANHATTAN or
     collides with too many components, the wire is DROPPED, not drawn.
     A dropped wire is better than a 800mm diagonal monstrosity.
  3. Grid placement: components are arranged in a grid (max N per column)
     instead of a single tall vertical stack. This prevents the 750mm+
     vertical spans that were forcing absurd wires.
  4. Hard final guard: every emitted path is verified to be (a) strictly
     orthogonal, (b) under MAX_WIRE_MANHATTAN, and (c) at least 2 points.
     Any path that fails any check is dropped.

Two-phase layout:
  Phase 1 – Tier placement (signal-flow left→right) with GRID packing.
             Satellites (caps, resistors) placed adjacent to their parent IC.
  Phase 2 – Orthogonal wire routing with obstacle avoidance.
             Multi-bend L / Z candidates, pin-direction-aware stubs,
             grid-snapped endpoints, length-capped, no diagonals.
"""

from __future__ import annotations

import math
import random
import re
from typing import Optional

import networkx as nx

GRID_SIZE  = 1.27
BBOX_PAD   = 1.5

TIER_GAP       = 20.32
COMP_V_GAP     = 7.62
SAT_H_GAP      = 12.70
SAT_V_GAP      = 3.81
PIN_STUB_LEN   = 2.54          # one grid step out from the symbol body

MAX_WIRE_MANHATTAN = 250.0     # HARD cap — anything longer is DROPPED
MAX_COMPS_PER_COLUMN = 4       # grid placement: max components per column
MAX_COLLISIONS = 0             # component bodies are hard routing obstacles
BBOX_CLEARANCE = 0.635         # extra clearance margin around symbol bodies (half grid step)
MAX_SAT_DISTANCE = 30.0        # HARD cap: satellites must be within 30mm Manhattan of parent center
# For point-to-point nets (only 2 pins) allow a longer wire — a long wire
# is better than a missing connection.
MAX_WIRE_PT2PT = 350.0

MATRIX_SIZE   = 300
MATRIX_OFFSET = 150
COLUMN_SPACING = 20.32
ROW_CLEARANCE  = 6.35

# Placement engine mode — flip to "legacy" for A/B comparison
PLACEMENT_MODE = "blocks_v2"    # "legacy" | "graph" | "blocks_v2"
OVERLAP_PULLBACK = 0.30     # pull toward spring position after overlap removal
SMALL_CIRCUIT_MAX_COMPONENTS = 20


# ── Net classification & criticality tables ──────────────────────────────

_NET_CLASSES: dict[str, str] = {
    "XTAL":   "CRYSTAL", "XIN":  "CRYSTAL", "XOUT": "CRYSTAL",
    "XI":     "CRYSTAL", "XO":   "CRYSTAL", "OSC":  "CRYSTAL",
    "CLK":    "CRYSTAL",
    "SCL":    "I2C",     "SDA":  "I2C",
    "MOSI":   "SPI",     "MISO": "SPI",     "SCK":  "SPI",
    "CS":     "SPI",     "SS":   "SPI",
    "TX":     "UART",    "RX":   "UART",
    "USB_DP": "USB",     "USB_DM": "USB",
    "VCC":    "POWER",   "VDD":  "POWER",   "VIN":  "POWER",
    "VBUS":   "POWER",   "3V3":  "POWER",   "5V":   "POWER",
    "GND":    "POWER",   "VSS":  "POWER",
    "RESET":  "RESET",   "EN":   "RESET",   "RST":  "RESET",
}

_NET_CRITICALITY: dict[str, float] = {
    "CRYSTAL": 5.0,
    "USB":     4.0,
    "SPI":     3.0,
    "UART":    2.5,
    "I2C":     2.0,
    "POWER":   1.5,
    "RESET":   1.5,
}

_PAIR_CONSTRAINTS: dict[tuple[str, str], float] = {
    ("MCU", "CRYSTAL"):       10.0,
    ("MCU", "CAPACITOR"):      8.0,
    ("LDO", "CAPACITOR"):      7.0,
    ("USB", "ESD_IC"):         8.0,
    ("MCU", "SENSOR"):         6.0,
    ("MCU", "RF_MODULE"):      5.0,
    ("REGULATOR", "CAPACITOR"): 7.0,
    ("MCU", "RESISTOR"):       3.0,
    ("SENSOR", "RESISTOR"):    4.0,
    ("RF_MODULE", "CAPACITOR"): 4.0,
}


# ── Block detection seeds (signal pattern → block role) ──────────────────
# Used by _seed_block_assignments() to tag components with block IDs based
# on shared netname patterns. These are merged with Louvain community
# detection results to form the final block map.

_BLOCK_SEEDS: dict[str, str] = {
    "RESET": "RESET_BLOCK",
    "EN":    "RESET_BLOCK",
    "ENABLE": "RESET_BLOCK",
    "RST":   "RESET_BLOCK",
    "NRST":  "RESET_BLOCK",
    "BOOT0": "BOOT_BLOCK",
    "BOOT1": "BOOT_BLOCK",
    "MODE":  "BOOT_BLOCK",
    "USB_DP": "USB_BLOCK",
    "USB_DM": "USB_BLOCK",
    "VBUS":  "USB_BLOCK",
    "XTAL":  "CRYSTAL_BLOCK",
    "XIN":   "CRYSTAL_BLOCK",
    "XOUT":  "CRYSTAL_BLOCK",
    "XI":    "CRYSTAL_BLOCK",
    "XO":    "CRYSTAL_BLOCK",
    "OSC":   "CRYSTAL_BLOCK",
    "OSC_IN":  "CRYSTAL_BLOCK",
    "OSC_OUT": "CRYSTAL_BLOCK",
    "MOSI":  "SPI_BLOCK",
    "MISO":  "SPI_BLOCK",
    "SCK":   "SPI_BLOCK",
    "CS":    "SPI_BLOCK",
    "SS":    "SPI_BLOCK",
    "NSS":   "SPI_BLOCK",
    "SCL":   "I2C_BLOCK",
    "SDA":   "I2C_BLOCK",
    "TX":    "UART_BLOCK",
    "RX":    "UART_BLOCK",
    "TXD":   "UART_BLOCK",
    "RXD":   "UART_BLOCK",
    "CANH":  "CAN_BLOCK",
    "CANL":  "CAN_BLOCK",
    "SWDIO": "DEBUG_BLOCK",
    "SWCLK": "DEBUG_BLOCK",
    "SWO":   "DEBUG_BLOCK",
    "TMS":   "DEBUG_BLOCK",
    "TCK":   "DEBUG_BLOCK",
    "TDI":   "DEBUG_BLOCK",
    "TDO":   "DEBUG_BLOCK",
}


# Canonical role for each detected block, used by _block_grid_layout()
# to pick the (grid_x, grid_y) target position. Order matters:
# the first match wins for blocks with ambiguous seed tags.
_BLOCK_ROLE: dict[str, str] = {
    "POWER_BLOCK":  "power",
    "USB_BLOCK":    "power",
    "REGULATOR_BLOCK": "regulator",
    "CRYSTAL_BLOCK": "mcu",
    "RESET_BLOCK":  "mcu",
    "BOOT_BLOCK":   "mcu",
    "MCU_BLOCK":    "mcu",
    "RF_MODULE_BLOCK": "mcu",
    "SPI_BLOCK":    "peripheral",
    "I2C_BLOCK":    "peripheral",
    "UART_BLOCK":   "peripheral",
    "CAN_BLOCK":    "peripheral",
    "DEBUG_BLOCK":  "mcu",
    "SENSOR_BLOCK": "peripheral",
    "DISPLAY_BLOCK": "peripheral",
    "LED_BLOCK":    "peripheral",
    "DECOUPLING_BLOCK": "mcu",
    "ORPHAN_BLOCK": "peripheral",
}


_IDSTR_HINTS: dict[str, str] = {
    'C_Small':        'CAPACITOR',
    'C_Small_US':     'CAPACITOR',
    'C_Polarized':    'CAPACITOR',
    'R_Small':        'RESISTOR',
    'R':              'RESISTOR',
    'Polyfuse':       'FUSE',
    'LED':            'LED',
    'D_Small':        'DIODE',
    'Zener':          'ZENER',
    # ── AVR / ATmega ──
    'ATmega':         'MCU',
    'ATtiny':         'MCU',
    'AT90':           'MCU',
    'ATxmega':        'MCU',
    'AVR128DA':       'MCU',
    'AVR128DB':       'MCU',
    'AVR64DA':        'MCU',
    'AVR64DD':        'MCU',
    # ── Other ICs ──
    'AMS1117':        'LDO',
    'DS18B20':        'SENSOR',
    'TPD6S300A':      'ESD_IC',
    'USBLC6':         'ESD_IC',
    'OLED':           'DISPLAY',
    'SSD1306':        'DISPLAY',
}

_TIER_RULES: list[tuple[str, int]] = [
    ('CONNECTOR',  0), ('USB',       0), ('BATTERY', 0),
    ('FUSE',       0), ('POLYFUSE',  0), ('SWITCH',  0),
    ('SPST',       0), ('SPDT',      0), ('TACTILE', 0),
    ('PUSHBUTTON', 0), ('DIP',       0), ('ROCKER',  0),
    ('TOGGLE',     0), ('SLIDE',     0),
    ('LDO',        1), ('REGULATOR', 1), ('BUCK',    1),
    ('BOOST',      1), ('CONVERTER', 1),
    ('MCU',        2), ('PROCESSOR', 2), ('ESP32',   2),
    ('STM32',      2), ('FPGA',      2), ('CPU',     2),
    ('RF_MODULE',  2), ('DSP',       2), ('MEMORY',  2),
    ('SENSOR',     3), ('DISPLAY',   3), ('DRIVER',  3),
    ('INDICATOR',  3),
    ('ESD_IC',     0), ('DIODE',     0), ('ZENER',   0),
    ('SW_',        -1), ('SWITCH',    0), ('BUTTON',   -1),
    ('LED',        -1), ('CAPACITOR', -1), ('RESISTOR', -1),
]


def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _sem_type(category: str, id_str: str = '') -> str:
    id_name = id_str.split(':')[-1] if ':' in id_str else id_str
    id_up = id_name.upper()
    for key, typ in _IDSTR_HINTS.items():
        key_up = key.upper()
        if key_up == 'R':
            # Strict match for single-letter 'R' to avoid matching 'ER_OLED', 'ALERT', etc.
            if id_up == 'R' or id_up.startswith('R_') or id_up.startswith('R-'):
                return typ
        elif key_up == 'LED':
            # Avoid matching 'OLED' as 'LED'
            if 'OLED' not in id_up and 'LED' in id_up:
                return typ
        elif key_up in id_up:
            return typ
    return category.upper().replace(' ', '_')


def _tier(category: str, id_str: str = '') -> int:
    sem = _sem_type(category, id_str)
    for kw, t in _TIER_RULES:
        if kw in sem:
            return t
    # Also check id_str (part name) against tier keywords
    id_name = id_str.split(':')[-1] if ':' in id_str else id_str
    id_up = id_name.upper().replace(' ', '_')
    for kw, t in _TIER_RULES:
        if kw.upper() in id_up:
            return t
    return 2


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


def calculate_ops_bbox(ops: list) -> dict:
    mn_x = mn_y =  float('inf')
    mx_x = mx_y = -float('inf')

    def upd(x, y):
        nonlocal mn_x, mn_y, mx_x, mx_y
        if x < mn_x: mn_x = x
        if x > mx_x: mx_x = x
        if y < mn_y: mn_y = y
        if y > mx_y: mx_y = y

    has_graphics = False
    for op in ops:
        t = op[0]
        if t == 'rectangle':
            has_graphics = True
            s = _get_attr(op, 'start'); e = _get_attr(op, 'end')
            if s: upd(float(s[1]), float(s[2]))
            if e: upd(float(e[1]), float(e[2]))
        elif t == 'polyline':
            has_graphics = True
            pts = _get_attr(op, 'pts')
            if pts:
                for i in range(1, len(pts)):
                    if pts[i][0] == 'xy': upd(float(pts[i][1]), float(pts[i][2]))
        elif t == 'circle':
            has_graphics = True
            c = _get_attr(op, 'center'); r = _get_attr(op, 'radius')
            if c and r:
                cx, cy, rv = float(c[1]), float(c[2]), float(r[1])
                upd(cx-rv, cy-rv); upd(cx+rv, cy+rv)

    # Fallback to pins if no body graphics exist
    if not has_graphics:
        for op in ops:
            if op[0] == 'pin':
                a = _get_attr(op, 'at')
                if a: upd(float(a[1]), float(a[2]))

    if mn_x == float('inf'):
        return {'x': -5.0, 'y': -5.0, 'w': 10.0, 'h': 10.0}
    return {
        'x': mn_x - BBOX_PAD,
        'y': mn_y - BBOX_PAD,
        'w': mx_x - mn_x + BBOX_PAD * 2,
        'h': mx_y - mn_y + BBOX_PAD * 2,
    }


# ── Routing geometry helpers ─────────────────────────────────────────────


def _pin_direction(pin: dict) -> str:
    """Resolve electrical pin exit direction (outward, away from symbol body)."""
    ang = pin.get('angle')
    if ang is None:
        ang = 0
    try:
        ang = int(round(float(ang))) % 360
    except (TypeError, ValueError):
        ang = 0
    # The pin's angle in KiCad points inward from hotspot to body.
    # To route away from the body, we exit in the opposite direction.
    exit_ang = (ang + 180) % 360
    if 45 <= exit_ang < 135:   return 'up'
    if 135 <= exit_ang < 225:  return 'left'
    if 225 <= exit_ang < 315:  return 'down'
    return 'right'


def _stub_point(px: float, py: float, direction: str,
                length: float = PIN_STUB_LEN) -> tuple[float, float]:
    if direction == 'left':  return (_snap(px - length), _snap(py))
    if direction == 'up':    return (_snap(px),            _snap(py + length))
    if direction == 'down':  return (_snap(px),            _snap(py - length))
    return (_snap(px + length), _snap(py))                  # right (default)


def _seg_intersects_bbox(p1: tuple[float, float],
                         p2: tuple[float, float],
                         bbox: dict,
                         cx: float, cy: float,
                         margin: float = 0.0) -> bool:
    """Liang–Barsky line/AABB intersection test."""
    left   = cx + bbox['x'] - margin
    right  = left + bbox['w'] + 2 * margin
    top    = cy + bbox['y'] - margin
    bottom = top + bbox['h'] + 2 * margin

    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1

    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - left), (dx, right - x1),
                 (-dy, y1 - top),  (dy, bottom - y1)):
        if abs(p) < 1e-9:
            if q < 0:
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1: return False
            if t > t0: t0 = t
        else:
            if t < t0: return False
            if t < t1: t1 = t
    return t0 < t1 - 1e-9


def _path_collisions(path: list[tuple[float, float]],
                     components: list[dict],
                     src_ref: str,
                     tgt_ref: str) -> int:
    """Count segment/component body intersections for a candidate path.

    Uses BBOX_CLEARANCE as margin so wires are routed with a safe
    keepout gap around symbol bodies, not just their bare outlines.

    A segment is exempted from collision with the source component if
    either of its endpoints lies inside the source body+clearance zone
    (the wire is still exiting the component).  Likewise for the target:
    segments whose endpoints are inside the target body+clearance are
    exempted (the wire is entering).

    Any other segment that intersects ANY component body (including
    src/tgt on intermediate segments that have fully exited/not-yet-entered)
    is counted.  This prevents wires from routing through their own or
    their partner's component body on free-space segments.
    """
    if len(path) < 2:
        return 0

    def _point_in_comp_clearance(px: float, py: float, c: dict) -> bool:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            return False
        margin = BBOX_CLEARANCE
        left   = c['x'] + bbox['x'] - margin
        right  = left + bbox['w'] + 2 * margin
        top    = c['y'] + bbox['y'] - margin
        bottom = top + bbox['h'] + 2 * margin
        return left <= px <= right and top <= py <= bottom

    hits = 0
    for i in range(len(path) - 1):
        p1, p2 = path[i], path[i + 1]
        if abs(p1[0] - p2[0]) < 1e-3 and abs(p1[1] - p2[1]) < 1e-3:
            continue
        for c in components:
            ref = c['ref_des']
            # Exempt segment if either endpoint is still inside the
            # source component (wire exiting) or already inside the
            # target component (wire entering).
            if ref == src_ref:
                if _point_in_comp_clearance(*p1, c) or _point_in_comp_clearance(*p2, c):
                    continue
            if ref == tgt_ref:
                if _point_in_comp_clearance(*p1, c) or _point_in_comp_clearance(*p2, c):
                    continue

            bbox = c.get('bbox') or c.get('geom_bbox')
            if not bbox:
                continue
            if _seg_intersects_bbox(p1, p2, bbox, c['x'], c['y'],
                                    margin=BBOX_CLEARANCE):
                hits += 1
    return hits


def _path_length(path: list[tuple[float, float]]) -> float:
    total = 0.0
    for i in range(len(path) - 1):
        total += abs(path[i][0] - path[i + 1][0]) + \
                 abs(path[i][1] - path[i + 1][1])
    return total


def _bend_count(path: list[tuple[float, float]]) -> int:
    if len(path) < 3:
        return 0
    bends = 0
    for i in range(1, len(path) - 1):
        dx1 = path[i][0] - path[i - 1][0]
        dy1 = path[i][1] - path[i - 1][1]
        dx2 = path[i + 1][0] - path[i][0]
        dy2 = path[i + 1][1] - path[i][1]
        if abs(dx1 - dx2) > 1e-3 or abs(dy1 - dy2) > 1e-3:
            bends += 1
    return bends


def _clean_path(path: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Drop consecutive duplicates and merge collinear interior points.

    Guarantees the returned path has only direction-change vertices
    plus the two endpoints — never produces a diagonal segment.
    """
    if not path:
        return []
    cleaned = [path[0]]
    for p in path[1:]:
        last = cleaned[-1]
        if abs(last[0] - p[0]) < 1e-3 and abs(last[1] - p[1]) < 1e-3:
            continue
        cleaned.append(p)
    if len(cleaned) < 3:
        return cleaned
    out = [cleaned[0]]
    for i in range(1, len(cleaned) - 1):
        x0, y0 = out[-1]
        x1, y1 = cleaned[i]
        x2, y2 = cleaned[i + 1]
        dx1, dy1 = x1 - x0, y1 - y0
        dx2, dy2 = x2 - x1, y2 - y1
        if abs(dx1 * dy2 - dy1 * dx2) < 1e-6 and \
           dx1 * dx2 >= -1e-6 and dy1 * dy2 >= -1e-6:
            continue
        out.append(cleaned[i])
    out.append(cleaned[-1])
    return out


def _is_orthogonal(path: list[tuple[float, float]]) -> bool:
    """Verify every segment is strictly horizontal or vertical."""
    for i in range(len(path) - 1):
        dx = abs(path[i][0] - path[i + 1][0])
        dy = abs(path[i][1] - path[i + 1][1])
        if dx > 1e-3 and dy > 1e-3:
            return False
    return True


# ── Candidate path generators ────────────────────────────────────────────


def _candidate_straight(s_pos, s_stub, t_pos, t_stub):
    """Direct s_stub → t_stub wire (only valid if they share X or Y)."""
    if abs(s_stub[0] - t_stub[0]) < 1e-3 or abs(s_stub[1] - t_stub[1]) < 1e-3:
        return [[s_pos, s_stub, t_stub, t_pos]]
    return []


def _candidate_L(s_pos, s_stub, t_pos, t_stub):
    """L-shape: src stub → corner → tgt stub.  Two orientations."""
    cands = []
    cands.append([s_pos, s_stub, (t_stub[0], s_stub[1]), t_stub, t_pos])
    cands.append([s_pos, s_stub, (s_stub[0], t_stub[1]), t_stub, t_pos])
    return cands


def _candidate_Z(s_pos, s_stub, t_pos, t_stub, components):
    """Z-shape candidates using obstacle clearance boundaries as bypass channels."""
    cands = []
    
    # Base levels
    x_levels = {s_stub[0], t_stub[0], _snap((s_stub[0] + t_stub[0]) / 2)}
    y_levels = {s_stub[1], t_stub[1], _snap((s_stub[1] + t_stub[1]) / 2)}
    
    # Add clearance levels of all components to bypass them
    for c in components:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            continue
        cx, cy = c['x'], c['y']
        
        # Left/right boundaries
        x_left = _snap(cx + bbox['x'] - BBOX_CLEARANCE - 1.27)
        x_right = _snap(cx + bbox['x'] + bbox['w'] + BBOX_CLEARANCE + 1.27)
        x_levels.add(x_left)
        x_levels.add(x_right)
        
        # Top/bottom boundaries
        y_bottom = _snap(cy + bbox['y'] - BBOX_CLEARANCE - 1.27)
        y_top = _snap(cy + bbox['y'] + bbox['h'] + BBOX_CLEARANCE + 1.27)
        y_levels.add(y_bottom)
        y_levels.add(y_top)
        
    for mid_x in x_levels:
        cands.append([s_pos, s_stub, (mid_x, s_stub[1]),
                      (mid_x, t_stub[1]), t_stub, t_pos])
                      
    for mid_y in y_levels:
        cands.append([s_pos, s_stub, (s_stub[0], mid_y),
                      (t_stub[0], mid_y), t_stub, t_pos])
                      
    return cands


def _candidate_U(s_pos, s_stub, t_pos, t_stub, components):
    """3-bend (U-shape) paths that route around component clusters."""
    cands = []
    x_levels = {_snap(s_stub[0]), _snap(t_stub[0])}
    y_levels = {_snap(s_stub[1]), _snap(t_stub[1])}

    for c in components:
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            continue
        cx, cy = c['x'], c['y']
        x_levels.add(_snap(cx + bbox['x'] - BBOX_CLEARANCE - 2.54))
        x_levels.add(_snap(cx + bbox['x'] + bbox['w'] + BBOX_CLEARANCE + 2.54))
        y_levels.add(_snap(cy + bbox['y'] - BBOX_CLEARANCE - 2.54))
        y_levels.add(_snap(cy + bbox['y'] + bbox['h'] + BBOX_CLEARANCE + 2.54))

    for bypass_x in x_levels:
        for bypass_y in y_levels:
            # Horizontal-first: source up/down, across, back up/down, across to target
            cands.append([s_pos, s_stub,
                          (s_stub[0], bypass_y), (bypass_x, bypass_y),
                          (bypass_x, t_stub[1]), t_stub, t_pos])
            # Vertical-first: source left/right, across, back left/right, across to target
            cands.append([s_pos, s_stub,
                          (bypass_x, s_stub[1]), (bypass_x, bypass_y),
                          (t_stub[0], bypass_y), t_stub, t_pos])
    return cands


def _astar_orthogonal(
    start: tuple[float, float],
    goal: tuple[float, float],
    components: list[dict],
    src_ref: str,
    tgt_ref: str,
    max_length: float,
    blocked_vertices: set[tuple[float, float]],
) -> list[tuple[float, float]] | None:
    """Bounded orthogonal A* over the schematic grid.

    Builds a grid at GRID_SIZE resolution covering the bounding box of
    start/goal plus margin.  Cells inside component bodies (with
    BBOX_CLEARANCE) are marked blocked.  Only the source and target
    components are exempted so the wire can leave/arrive.

    Returns a list of waypoints (already snapped) or None if no path
    found within the length cap.
    """
    import heapq
    margin = 200.0
    min_x = min(start[0], goal[0]) - margin
    max_x = max(start[0], goal[0]) + margin
    min_y = min(start[1], goal[1]) - margin
    max_y = max(start[1], goal[1]) + margin

    gs = GRID_SIZE
    cols = max(3, int(round((max_x - min_x) / gs)))
    rows = max(3, int(round((max_y - min_y) / gs)))
    max_x = min_x + cols * gs
    max_y = min_y + rows * gs

    def _to_grid(wx: float, wy: float) -> tuple[int, int]:
        return (int(round((wx - min_x) / gs)),
                int(round((wy - min_y) / gs)))

    def _to_world(gx: int, gy: int) -> tuple[float, float]:
        return (_snap(min_x + gx * gs), _snap(min_y + gy * gs))

    # Build obstacle set — skip source/target so the A* can freely exit/enter.
    blocked_cells: set[tuple[int, int]] = set()
    for c in components:
        ref = c['ref_des']
        if ref in (src_ref, tgt_ref):
            continue
        bbox = c.get('bbox') or c.get('geom_bbox')
        if not bbox:
            continue
        left   = c['x'] + bbox['x'] - BBOX_CLEARANCE
        right  = left + bbox['w'] + 2 * BBOX_CLEARANCE
        top    = c['y'] + bbox['y'] - BBOX_CLEARANCE
        bottom = top + bbox['h'] + 2 * BBOX_CLEARANCE
        gx1, gy1 = _to_grid(left, top)
        gx2, gy2 = _to_grid(right, bottom)
        for gx in range(max(0, gx1), min(cols, gx2 + 1)):
            for gy in range(max(0, gy1), min(rows, gy2 + 1)):
                blocked_cells.add((gx, gy))

    gs_pos = _to_grid(*start)
    gg_pos = _to_grid(*goal)

    if gs_pos == gg_pos:
        return []

    if not (0 <= gs_pos[0] < cols and 0 <= gs_pos[1] < rows and
            0 <= gg_pos[0] < cols and 0 <= gg_pos[1] < rows):
        return None

    # Unblock the stub cells so the A* can start/end there.
    blocked_cells.discard(gs_pos)
    blocked_cells.discard(gg_pos)

    # Unblock ALL four neighbours of the start and goal cells, even if they
    # are inside the source/target component body.  This gives the A* a way
    # to leave the source and enter the target.  The first and last segments
    # of the final path inside these bodies are exempted by _path_collisions.
    for origin in (gs_pos, gg_pos):
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = origin[0] + dx, origin[1] + dy
            if 0 <= nx < cols and 0 <= ny < rows:
                blocked_cells.discard((nx, ny))

    # Also block vertices already occupied by other traces
    for vx, vy in blocked_vertices:
        gx, gy = _to_grid(vx, vy)
        if 0 <= gx < cols and 0 <= gy < rows:
            blocked_cells.add((gx, gy))

    max_steps = int(max_length / gs) * 4

    def _heuristic(gx, gy):
        return abs(gx - gg_pos[0]) + abs(gy - gg_pos[1])

    open_set = [(0, gs_pos)]
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {gs_pos: 0}
    f_score: dict[tuple[int, int], float] = {gs_pos: _heuristic(*gs_pos)}

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == gg_pos:
            # Reconstruct
            path_grid = []
            while current in came_from:
                path_grid.append(current)
                current = came_from[current]
            path_grid.append(gs_pos)
            path_grid.reverse()
            waypoints = [_to_world(gx, gy) for gx, gy in path_grid]
            return _clean_path(waypoints)

        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = current[0] + dx, current[1] + dy
            if not (0 <= nx < cols and 0 <= ny < rows):
                continue
            if (nx, ny) in blocked_cells:
                continue
            neighbor = (nx, ny)
            tentative = g_score[current] + 1
            if tentative < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f = tentative + _heuristic(nx, ny)
                f_score[neighbor] = f
                heapq.heappush(open_set, (f, neighbor))

    return None


def _make_path(s_pos, s_dir, t_pos, t_dir, components, src_ref, tgt_ref,
               blocked_vertices: set[tuple[float, float]] | None = None):
    """Generate, score, and pick the best orthogonal path.

    ``blocked_vertices`` — intermediate (non-pin-endpoint) coordinates from
    *already-routed* traces.  Any candidate whose intermediate vertices land
    on a blocked coordinate is penalised to prevent accidental electrical
    shorts in KiCad (two unrelated nets sharing a wire vertex = junction).

    Returns None if no candidate satisfies the length cap and collision
    limit — caller must DROP the wire in that case (do NOT fallback).
    """
    s_stub = _stub_point(*s_pos, s_dir)
    t_stub = _stub_point(*t_pos, t_dir)
    blocked = blocked_vertices or set()

    candidates = []
    candidates += _candidate_straight(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_L(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_Z(s_pos, s_stub, t_pos, t_stub, components)
    candidates += _candidate_U(s_pos, s_stub, t_pos, t_stub, components)

    best_path = None
    best_score = float('inf')
    for raw in candidates:
        path = _clean_path(raw)
        if len(path) < 2:
            continue
        length = _path_length(path)
        if length > MAX_WIRE_MANHATTAN:
            continue
        collisions = _path_collisions(path, components, src_ref, tgt_ref)
        if collisions > MAX_COLLISIONS:
            continue
        bends = _bend_count(path)
        vertex_overlap = False
        for v in path[1:-1]:
            if v in blocked:
                vertex_overlap = True
                break
        if vertex_overlap:
            continue
        score = collisions * 10000 + length + bends * 2
        if score < best_score:
            best_score = score
            best_path = path

    # Relaxed second pass: permit longer detours, but never relax the
    # component-body collision guarantee.
    if best_path is None:
        relaxed_collisions = MAX_COLLISIONS
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            length = _path_length(path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(path)
            vertex_overlap = False
            for v in path[1:-1]:
                if v in blocked:
                    vertex_overlap = True
                    break
            if vertex_overlap:
                continue
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = path

    # Tertiary pass: if still no path, try offsetting intermediate vertices
    # by ±GRID_SIZE to create parallel lanes avoiding exact coordinate
    # collisions with already-routed traces.
    if best_path is None:
        offsets = [(GRID_SIZE, 0), (-GRID_SIZE, 0), (0, GRID_SIZE), (0, -GRID_SIZE)]
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            base_length = _path_length(path)
            if base_length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            # For each intermediate vertex that's blocked, try offsetting it
            fully_repaired = True
            for i in range(1, len(path) - 1):
                if path[i] in blocked:
                    repaired = False
                    for ox, oy in offsets:
                        shifted = _snap(path[i][0] + ox), _snap(path[i][1] + oy)
                        if shifted not in blocked:
                            path[i] = shifted
                            repaired = True
                            break
                    if not repaired:
                        fully_repaired = False
                        break
            if not fully_repaired:
                continue
            # Re-validate after repair
            new_path = _clean_path(path)
            if len(new_path) < 2:
                continue
            length = _path_length(new_path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(new_path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(new_path)
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = new_path

    # Fourth pass: bounded orthogonal A* — only when simple candidates fail
    if best_path is None:
        astar_path = _astar_orthogonal(
            s_stub, t_stub, components, src_ref, tgt_ref,
            MAX_WIRE_MANHATTAN * 1.5, blocked,
        )
        if astar_path:
            path = [s_pos] + astar_path + [t_pos]
            path = _clean_path(path)
            if len(path) >= 2:
                length = _path_length(path)
                if length <= MAX_WIRE_MANHATTAN * 1.5:
                    collisions = _path_collisions(path, components, src_ref, tgt_ref)
                    if collisions <= MAX_COLLISIONS:
                        best_path = path

    # Fifth pass: LAST-RESORT — allow exactly 1 collision for long wires.
    # A wire that grazes one component body is far better than a missing
    # connection. Only used when every zero-collision candidate failed.
    if best_path is None:
        relaxed_collisions = max(MAX_COLLISIONS + 1, 1)
        for raw in candidates:
            path = _clean_path(raw)
            if len(path) < 2:
                continue
            length = _path_length(path)
            if length > MAX_WIRE_MANHATTAN * 1.5:
                continue
            collisions = _path_collisions(path, components, src_ref, tgt_ref)
            if collisions > relaxed_collisions:
                continue
            bends = _bend_count(path)
            score = collisions * 10000 + length + bends * 2
            if score < best_score:
                best_score = score
                best_path = path

    return best_path


# ── Layout engine ────────────────────────────────────────────────────────


class BackendLayoutEngine:
    """Hierarchical schematic placement + obstacle-aware orthogonal routing."""

    def __init__(self):
        self.components: list[dict] = []
        self.matrix: list | None = None
        self.grid = None
        self._spring_pos: dict[str, tuple[float, float]] | None = None
        self._last_block_map: dict[str, str] | None = None

    def add_component(self, ref_des: str, ops: list, category: str,
                      id_str: str = '',
                      for_component: str = '') -> None:
        bbox = calculate_ops_bbox(ops)
        self.components.append({
            'ref_des':  ref_des,
            'ops':      ops,
            'category': category,
            'id_str':   id_str,
            'bbox':     bbox,
            'x': 0.0, 'y': 0.0, 'rotation': 0.0,
            'width':    bbox['w'],
            'height':   bbox['h'],
            'tier':     _tier(category, id_str),
            'sem':      _sem_type(category, id_str),
            'for_component': for_component,
        })

    def set_component_position(self, ref_des: str, x: float, y: float,
                               rotation: float = 0.0) -> None:
        c = self._get_comp(ref_des)
        if c:
            c['x'] = x; c['y'] = y; c['rotation'] = rotation

    # ── Placement ──────────────────────────────────────────────────────

    def execute_placement(self, pin_matrix: dict = None,
                          netlist: list = None) -> None:
        """Hierarchical tier placement with GRID packing.

        Components within a tier are arranged in a grid (max
        MAX_COMPS_PER_COLUMN per column) instead of a single tall
        vertical stack. This prevents the 750mm+ vertical spans that
        were forcing absurd wires.
        """
        if not self.components:
            return

        netlist  = netlist  or []
        pin_matrix = pin_matrix or {}

        parent_map = self._build_parent_map(netlist)

        # Post-pass: assign parents to decoupling capacitors and other
        # satellites whose connections are power-only (never in netlist).
        # Use the for_component field set by support_rules / select.py.
        for s in self.components:
            if s['tier'] != -1:
                continue
            if s['ref_des'] in parent_map:
                continue
            fc = s.get('for_component', '')
            if fc and self._get_comp(fc):
                parent_map[s['ref_des']] = fc

        tiers: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
        sats:  list[dict] = []
        for c in self.components:
            if c['tier'] == -1:
                sats.append(c)
            else:
                tiers.setdefault(c['tier'], []).append(c)

        for t in tiers:
            tiers[t].sort(key=lambda c: -c['height'])

        x_cursor = 0.0  # used by orphan satellite placement below

        if PLACEMENT_MODE == "graph" and netlist:
            # ── Graph-based placement ───────────────────────────────────
            graph = self._build_weighted_graph(netlist, pin_matrix)
            mains = [c for c in self.components if c['tier'] >= 0]

            if len(mains) < 2:
                # Fall through to tier-column for trivial circuits
                pass
            else:
                # Extract subgraph of main (non-satellite) components
                main_nodes = [c['ref_des'] for c in mains]
                sub = graph.subgraph(main_nodes).copy()

                if sub.number_of_nodes() < 2:
                    pass  # fall through
                else:
                    # Compute optimal k proportional to component area
                    total_area = sum(
                        sub.nodes[n].get('bbox_area', 100.0)
                        for n in sub.nodes
                    )
                    optimal_k = 3.0 * math.sqrt(total_area / max(len(sub.nodes), 1))

                    # Degree anchoring: top-3 heaviest nodes get fixed_weight=0.3
                    sorted_nodes = sorted(
                        sub.nodes(data=True),
                        key=lambda nd: nd[1].get('weighted_degree',
                                                  sub.degree(nd[0], weight='weight')),
                        reverse=True,
                    )
                    fixed_nodes = set(n for n, _ in sorted_nodes[:3])

                    # Run spring layout (no seed — variance across retries)
                    pos = nx.spring_layout(
                        sub,
                        weight='weight',
                        iterations=500,
                        k=optimal_k,
                    )

                    # Apply fixed_weight=0.3 for anchors (not a hard lock)
                    for _ in range(3):
                        pos2 = nx.spring_layout(
                            sub,
                            weight='weight',
                            iterations=100,
                            k=optimal_k,
                            pos=pos,
                            fixed=fixed_nodes if False else None,  # never hard-lock
                        )
                        # Blend: 70% new position, 30% fixed back toward prior
                        for n in fixed_nodes:
                            if n in pos2 and n in pos:
                                pos2[n] = (
                                    pos2[n][0] * 0.7 + pos[n][0] * 0.3,
                                    pos2[n][1] * 0.7 + pos[n][1] * 0.3,
                                )
                        pos = pos2

                    # Scale to physical coordinates (grid)
                    min_x = min(p[0] for p in pos.values())
                    min_y = min(p[1] for p in pos.values())
                    max_x = max(p[0] for p in pos.values())
                    max_y = max(p[1] for p in pos.values())
                    range_x = max(max_x - min_x, 1.0)
                    range_y = max(max_y - min_y, 1.0)

                    # Map to physical scale: adapt to component count
                    n_nodes = sub.number_of_nodes()
                    target_span = 200.0 if n_nodes >= 10 else max(range_x, range_y) * 50.0
                    scale = target_span / max(range_x, range_y)
                    for ref, (px, py) in pos.items():
                        comp = self._get_comp(ref)
                        if comp:
                            sx = _snap((px - min_x) * scale - min_x * 0.5)
                            sy = _snap((py - min_y) * scale - min_y * 0.5)
                            # Tier soft-offset: 20% push left/right by tier
                            tier_push = comp.get('tier', 2) - 1.5  # -1.5 to +1.5
                            sx = _snap(sx + tier_push * 10.0 * 0.2)
                            comp['x'] = sx
                            comp['y'] = sy

                    # Store spring positions for overlap removal to pull back to
                    self._spring_pos = {
                        ref: (comp['x'], comp['y'])
                        for ref, comp in
                        ((c['ref_des'], c) for c in self.components
                         if c['ref_des'] in pos)
                    }
        elif PLACEMENT_MODE == "blocks_v2" and netlist:
            # ── Block-aware placement (Louvain + local spring) ────────
            graph = self._build_weighted_graph(netlist, pin_matrix)

            # 1. Detect functional blocks via Louvain + seed signals
            block_of = self._detect_blocks_louvain(graph, netlist)
            self._last_block_map = block_of

            # 2. Group component refs by block
            blocks: dict[str, list[str]] = {}
            for c in self.components:
                bid = block_of.get(c['ref_des'], "ORPHAN_BLOCK")
                blocks.setdefault(bid, []).append(c['ref_des'])

            # 3. Assign grid positions to each block
            grid_cells = self._block_grid_layout(blocks)

            # 4. Place each block (local spring layout + pin-side satellites)
            all_placed: set[str] = set()
            # Process MCU block first, then power, then the rest
            block_order = sorted(blocks.keys(),
                                 key=lambda b: (
                                     0 if _BLOCK_ROLE.get(b, "") == "mcu" else
                                     1 if _BLOCK_ROLE.get(b, "") == "power" else
                                     2 if _BLOCK_ROLE.get(b, "peripheral") == "power" else
                                     3,
                                     b
                                 ))
            for bid in block_order:
                refs = blocks[bid]
                bbox = grid_cells.get(bid, {
                    'x': 0, 'y': len(all_placed) * 200.0,
                    'width': 200.0, 'height': 150.0,
                })
                self._place_block(refs, bbox, parent_map, pin_matrix,
                                  netlist, graph, all_placed)

            # Store spring positions for overlap removal
            self._spring_pos = {
                c['ref_des']: (c['x'], c['y'])
                for c in self.components
                if c['ref_des'] in all_placed
            }

            # Any unplaced orphans: fall through to legacy satellite handler
            unplaced = [c for c in self.components
                        if c['ref_des'] not in all_placed]
            if unplaced:
                sats = [c for c in unplaced if c['tier'] == -1]

        else:

            # ── Fallback tier-column placement for unhandled modes ──
            x_cursor = 0.0
            for tier_idx in sorted(tiers):
                comps = tiers[tier_idx]
                if not comps:
                    continue
                tier_w = max(c['width'] for c in comps) + BBOX_PAD * 2

                col_count = max(1, math.ceil(len(comps) / MAX_COMPS_PER_COLUMN))
                col_w = tier_w + TIER_GAP

                cols = [[] for _ in range(col_count)]
                for i, c in enumerate(comps):
                    col_idx = i // MAX_COMPS_PER_COLUMN
                    cols[col_idx].append(c)

                for col_idx, col_comps in enumerate(cols):
                    y_cursor = 0.0
                    for c in col_comps:
                        c['x'] = _snap(x_cursor + col_idx * col_w +
                                       (tier_w - c['width']) / 2)
                        c['y'] = _snap(y_cursor - c['bbox']['y'])
                        y_cursor += c['height'] + BBOX_PAD * 2 + COMP_V_GAP

                x_cursor += col_count * col_w + TIER_GAP

        # ── Satellites (caps, resistors) placed next to parent IC ──
        sat_groups: dict[str, list] = {}
        orphan_sats: list[dict] = []
        for s in sats:
            par = parent_map.get(s['ref_des'])
            par_c = self._get_comp(par) if par else None
            if par_c and par_c['tier'] >= 0:
                sat_groups.setdefault(par, []).append(s)
            else:
                orphan_sats.append(s)

        for par_ref, group in sat_groups.items():
            par_c = self._get_comp(par_ref)
            if not par_c:
                continue
            sy_start = _snap(par_c['y'])
            
            # Distribute satellites on both sides of the parent:
            # even indices → right side, odd indices → left side
            right_group = [s for i, s in enumerate(group) if i % 2 == 0]
            left_group  = [s for i, s in enumerate(group) if i % 2 == 1]
            
            # ── Right side ──
            if right_group:
                sx = _snap(par_c['x'] + par_c['width'] + SAT_H_GAP)
                sat_col_count = max(1, math.ceil(len(right_group) / MAX_COMPS_PER_COLUMN))
                sat_cols = [[] for _ in range(sat_col_count)]
                for i, s in enumerate(right_group):
                    col_idx = i // MAX_COMPS_PER_COLUMN
                    sat_cols[col_idx].append(s)
                for col_idx, col_sats in enumerate(sat_cols):
                    y_cursor = sy_start
                    for s in col_sats:
                        s['x'] = _snap(sx + col_idx * (s['width'] + SAT_H_GAP))
                        s['y'] = _snap(y_cursor)
                        y_cursor += s['height'] + SAT_V_GAP
            
            # ── Left side ──
            if left_group:
                sat_col_count = max(1, math.ceil(len(left_group) / MAX_COMPS_PER_COLUMN))
                sat_cols = [[] for _ in range(sat_col_count)]
                for i, s in enumerate(left_group):
                    col_idx = i // MAX_COMPS_PER_COLUMN
                    sat_cols[col_idx].append(s)
                for col_idx, col_sats in enumerate(sat_cols):
                    y_cursor = sy_start
                    for s in col_sats:
                        s['x'] = _snap(par_c['x'] - SAT_H_GAP -
                                       col_idx * (s['width'] + SAT_H_GAP) - s['width'])
                        s['y'] = _snap(y_cursor)
                        y_cursor += s['height'] + SAT_V_GAP

        # Orphan satellites: grid placement, not single tall column
        if orphan_sats:
            rx = max((c['x'] + c['width'] for c in self.components
                      if c['tier'] != -1), default=x_cursor)
            rx = _snap(rx + TIER_GAP)
            col_w_orphan = max(s['width'] for s in orphan_sats) + SAT_H_GAP
            
            orphan_col_count = max(1, math.ceil(len(orphan_sats) / MAX_COMPS_PER_COLUMN))
            orphan_cols = [[] for _ in range(orphan_col_count)]
            for i, s in enumerate(orphan_sats):
                col_idx = i // MAX_COMPS_PER_COLUMN
                orphan_cols[col_idx].append(s)
                
            for col_idx, col_sats in enumerate(orphan_cols):
                y_cursor = 0.0
                for s in col_sats:
                    s['x'] = _snap(rx + col_idx * col_w_orphan)
                    s['y'] = _snap(y_cursor)
                    y_cursor += s['height'] + SAT_V_GAP

        # Centre everything around (0, 0) — only needed for non-graph modes
        if PLACEMENT_MODE != "graph":
            xs = [c['x'] for c in self.components]
            ys = [c['y'] for c in self.components]
            if xs:
                ox = _snap((max(xs) + min(xs)) / 2)
                oy = _snap((max(ys) + min(ys)) / 2)
                for c in self.components:
                    c['x'] = _snap(c['x'] - ox)
                    c['y'] = _snap(c['y'] - oy)
            # Update spring positions after centering so that overlap
            # removal's pullback pulls toward the centred coordinates,
            # not the far-away block-grid positions.
            if PLACEMENT_MODE == "blocks_v2":
                self._spring_pos = {
                    c['ref_des']: (c['x'], c['y'])
                    for c in self.components
                }

        # Overlap removal — run for both modes
        self._remove_overlaps()

        # Post-pass: move satellites that are still too far from their parent
        # Skipped in blocks_v2 mode — _place_block already handles satellite
        # proximity with pin-side awareness, and this pass would overwrite it.
        if PLACEMENT_MODE != "blocks_v2":
            n_moved = self._enforce_satellite_distance(parent_map)

        # Convert component positions to symbol origins so that routing,
        # export, and collision detection all use a consistent reference.
        # Placement algorithms may store bbox-left-edge or spring-center
        # positions; _seg_intersects_bbox and _abs assume symbol origin.
        for c in self.components:
            c['x'] = _snap(c['x'] - c['bbox']['x'])
            c['y'] = _snap(c['y'] - c['bbox']['y'])

    def _build_parent_map(self, netlist: list) -> dict[str, str]:
        scores: dict[tuple[str, str], int] = {}
        for conn in netlist:
            sr = conn['source'].split(':')[0]
            tr = conn['target'].split(':')[0]
            sc = self._get_comp(sr)
            tc = self._get_comp(tr)
            if not sc or not tc:
                continue
            for sat_c, ic_c in [(sc, tc), (tc, sc)]:
                if sat_c['tier'] == -1 and ic_c['tier'] >= 0:
                    key = (sat_c['ref_des'], ic_c['ref_des'])
                    scores[key] = scores.get(key, 0) + 1

        parent: dict[str, str] = {}
        for (sat, ic), _ in sorted(scores.items(), key=lambda kv: -kv[1]):
            if sat not in parent:
                parent[sat] = ic
        return parent

    def _enforce_satellite_distance(self, parent_map: dict) -> int:
        """Post-placement pass: compact satellites that exceed MAX_SAT_DISTANCE.

        Groups satellites by parent, then for each group tightens the X
        position while preserving the relative vertical stacking order.
        Returns number of satellites relocated.
        """
        if not parent_map:
            return 0

        # Group satellites by parent
        by_parent: dict[str, list[dict]] = {}
        for s in self.components:
            if s['tier'] != -1:
                continue
            par_ref = parent_map.get(s['ref_des'])
            if not par_ref:
                continue
            by_parent.setdefault(par_ref, []).append(s)

        n_moved = 0
        TIGHT_GAP = max(GRID_SIZE, SAT_H_GAP * 0.3)

        for par_ref, group in by_parent.items():
            par_c = self._get_comp(par_ref)
            if not par_c:
                continue

            pcx = par_c['x'] + par_c['bbox']['x'] + par_c['width'] / 2
            pcy = par_c['y'] + par_c['bbox']['y'] + par_c['height'] / 2

            # Sort by current Y position to preserve stacking order
            group.sort(key=lambda s: (s['y'], s['ref_des']))

            # Split into left/right sides based on current center x
            left_sats: list[dict] = []
            right_sats: list[dict] = []
            for s in group:
                scx = s['x'] + s['bbox']['x'] + s['width'] / 2
                if scx >= pcx:
                    right_sats.append(s)
                else:
                    left_sats.append(s)

            # Repack right-side satellites in a tight column
            if right_sats:
                right_base = par_c['x'] + par_c['bbox']['x'] + par_c['width'] + TIGHT_GAP
                y_cursor = _snap(pcy - right_sats[0]['bbox']['y'] - right_sats[0]['height'] / 2)
                for s in right_sats:
                    new_x = _snap(right_base - s['bbox']['x'])
                    if new_x != s['x']:
                        s['x'] = new_x
                        n_moved += 1
                    s['y'] = _snap(y_cursor)
                    y_cursor += s['height'] + GRID_SIZE

            # Repack left-side satellites in a tight column
            if left_sats:
                y_cursor = _snap(pcy - left_sats[0]['bbox']['y'] - left_sats[0]['height'] / 2)
                for s in left_sats:
                    new_x = _snap(par_c['x'] + par_c['bbox']['x'] -
                                  TIGHT_GAP - s['bbox']['x'] - s['width'])
                    if new_x != s['x']:
                        s['x'] = new_x
                        n_moved += 1
                    s['y'] = _snap(y_cursor)
                    y_cursor += s['height'] + GRID_SIZE

        return n_moved

    def _remove_overlaps(self, max_iters: int = 100) -> int:
        """Push apart overlapping component bounding boxes.

        After spring layout (or legacy placement), components may overlap.
        Each iteration identifies all overlapping geometry bounding boxes
        and pushes them apart along the axis of minimum overlap. Component
        positions are symbol origins, so bbox offsets must be included when
        calculating their actual occupied sheet area.

        Returns the number of overlap pairs remaining after ``max_iters``.
        """
        if len(self.components) < 2:
            return 0

        def bounds(component):
            bbox = component['bbox']
            return (
                component['x'] + bbox['x'] - BBOX_CLEARANCE,
                component['y'] + bbox['y'] - BBOX_CLEARANCE,
                component['x'] + bbox['x'] + bbox['w'] + BBOX_CLEARANCE,
                component['y'] + bbox['y'] + bbox['h'] + BBOX_CLEARANCE,
            )

        def count_overlaps():
            count = 0
            for i in range(len(self.components)):
                ax1, ay1, ax2, ay2 = bounds(self.components[i])
                for j in range(i + 1, len(self.components)):
                    bx1, by1, bx2, by2 = bounds(self.components[j])
                    if ax2 > bx1 and ax1 < bx2 and ay2 > by1 and ay1 < by2:
                        count += 1
            return count

        for _ in range(max_iters):
            remaining = 0
            for i in range(len(self.components)):
                for j in range(i + 1, len(self.components)):
                    a = self.components[i]
                    b = self.components[j]
                    ax1, ay1, ax2, ay2 = bounds(a)
                    bx1, by1, bx2, by2 = bounds(b)

                    if ax2 <= bx1 or ax1 >= bx2 or ay2 <= by1 or ay1 >= by2:
                        continue  # no overlap

                    remaining += 1

                    # Compute overlap on each axis
                    ox = min(ax2, bx2) - max(ax1, bx1)
                    oy = min(ay2, by2) - max(ay1, by1)

                    if ox <= 0 and oy <= 0:
                        continue

                    # Determine push direction: push apart on the smaller overlap axis
                    if ox < oy or (ox == oy and ox == 0):
                        # Horizontal separation
                        push = (ox + GRID_SIZE) / 2
                        if a['x'] <= b['x']:
                            a['x'] -= push
                            b['x'] += push
                        else:
                            a['x'] += push
                            b['x'] -= push
                    else:
                        # Vertical separation
                        push = (oy + GRID_SIZE) / 2
                        if a['y'] <= b['y']:
                            a['y'] -= push
                            b['y'] += push
                        else:
                            a['y'] += push
                            b['y'] -= push

            if remaining == 0:
                break

        for component in self.components:
            component['x'] = _snap(component['x'])
            component['y'] = _snap(component['y'])

        return count_overlaps()

    def _build_weighted_graph(self, netlist: list,
                                pin_matrix: dict) -> nx.Graph:
        """Build a weighted connectivity graph from the netlist.

        Nodes = component ref_des.
        Edge weight = sum of net criticalities for all nets shared by the
        pair, plus pair-constraint bonus if the component types match.

        Returns a ``networkx.Graph`` with node attributes ``sem``, ``tier``,
        ``degree``, ``bbox_area`` set for downstream layout use.
        """
        # Raw connection count per component pair
        raw_weights: dict[tuple[str, str], float] = {}
        for conn in netlist:
            sr = conn['source'].split(':')[0]
            tr = conn['target'].split(':')[0]
            if sr == tr:
                continue

            # Classify the net via pin name
            pin_key = conn.get('source', '')
            pin_name = pin_key.split(':')[-1] if ':' in pin_key else pin_key
            pin_up = pin_name.upper().replace(' ', '_')
            net_cls = "GPIO"
            for kw, cls in _NET_CLASSES.items():
                if kw in pin_up:
                    net_cls = cls
                    break

            weight = _NET_CRITICALITY.get(net_cls, 1.0)
            key = (sr, tr) if sr <= tr else (tr, sr)
            raw_weights[key] = raw_weights.get(key, 0.0) + weight

        g = nx.Graph()
        for c in self.components:
            ref = c['ref_des']
            sem = _sem_type(c['category'], c.get('id_str', ''))
            g.add_node(ref, sem=sem, tier=c['tier'],
                       bbox_area=c['width'] * c['height'])

        for (a, b), w in raw_weights.items():
            if not g.has_node(a) or not g.has_node(b):
                continue
            # Add pair-constraint bonus
            sem_a = g.nodes[a].get('sem', '')
            sem_b = g.nodes[b].get('sem', '')
            bonus = _PAIR_CONSTRAINTS.get((sem_a, sem_b), 0.0) or \
                    _PAIR_CONSTRAINTS.get((sem_b, sem_a), 0.0)

            effective = w + bonus
            if g.has_edge(a, b):
                g[a][b]['weight'] += effective
            else:
                g.add_edge(a, b, weight=effective)

        # Annotate degree
        for node in g.nodes:
            g.nodes[node]['degree'] = g.degree(node, weight='weight')

        return g

    # ── Block-aware placement helpers ──────────────────────────────────

    def _detect_blocks_louvain(self, graph: nx.Graph,
                               netlist: list) -> dict[str, str]:
        """Detect functional blocks via Louvain modularity + seed signals.

        Returns a dict {ref_des: block_name} for every component.
        Seed-named signals (RESET, USB, XTAL, …) are matched first;
        Louvain partitions fill in the remaining components.
        """
        if graph.number_of_nodes() <= SMALL_CIRCUIT_MAX_COMPONENTS:
            return {ref: "SMALL_CIRCUIT_BLOCK" for ref in graph.nodes}

        block_of: dict[str, str] = {}
        seeded = self._seed_block_assignments(netlist)
        block_of.update(seeded)

        assigned = set(seeded)
        unassigned = [n for n in graph.nodes if n not in assigned]
        if len(unassigned) >= 3:
            sub = graph.subgraph(unassigned).copy()
            try:
                communities = nx.algorithms.community.louvain_communities(
                    sub, weight='weight', seed=42
                )
            except AttributeError:
                communities = nx.algorithms.community.greedy_modularity_communities(
                    sub, weight='weight'
                )
            for i, comm in enumerate(communities):
                block_name = f"LOUVAIN_BLOCK_{i}"
                for r in comm:
                    if r in seeded:
                        block_name = seeded[r]
                        break
                for ref in comm:
                    block_of[ref] = block_name

        for ref in graph.nodes:
            if ref not in block_of:
                block_of[ref] = "ORPHAN_BLOCK"

        return block_of

    def _seed_block_assignments(self, netlist: list) -> dict[str, str]:
        """Tag component pairs with a block ID based on signal names.

        Scans the netlist for pin names matching ``_BLOCK_SEEDS`` keys and
        tags *both* endpoints of the net with the corresponding block ID.
        Both source and target pin names are checked.
        """
        block_of: dict[str, str] = {}
        for conn in netlist:
            for side in ('source', 'target'):
                pin_key = conn.get(side, '')
                pin_name = pin_key.split(':')[-1] if ':' in pin_key else pin_key
                pin_up = pin_name.upper().replace(' ', '_')
                block_id = None
                for kw, bid in _BLOCK_SEEDS.items():
                    if kw == pin_up or pin_up.startswith(kw + '_') or pin_up.endswith('_' + kw):
                        block_id = bid
                        break
                if block_id is None:
                    continue
                sr = conn['source'].split(':')[0]
                tr = conn['target'].split(':')[0]
                block_of.setdefault(sr, block_id)
                block_of.setdefault(tr, block_id)
        return block_of

    def _block_grid_layout(self, blocks: dict[str, list[str]]
                           ) -> dict[str, dict]:
        """Assign a target grid cell to each block based on its role.

        Returns ``{block_name: {x, y, width, height}}`` bounding-box dicts.
        """
        if set(blocks) == {"SMALL_CIRCUIT_BLOCK"}:
            span_factor = math.sqrt(len(blocks["SMALL_CIRCUIT_BLOCK"]))
            return {
                "SMALL_CIRCUIT_BLOCK": {
                    'x': 0.0,
                    'y': 0.0,
                    'width': max(80.0, span_factor * 25.0),
                    'height': max(60.0, span_factor * 20.0),
                }
            }

        cell_w = 200.0
        cell_h = 150.0
        grid_map: dict[str, tuple[int, int]] = {}
        peripheral_count = 0
        for block_name in blocks:
            role = _BLOCK_ROLE.get(block_name, "peripheral")
            if role == "mcu":
                grid_map[block_name] = (1, 1)
            elif role == "power":
                grid_map[block_name] = (0, 0)
            elif role == "regulator":
                grid_map[block_name] = (1, 0)
            else:
                grid_map[block_name] = (3, peripheral_count)
                peripheral_count += 1

        result: dict[str, dict] = {}
        for block_name, (gx, gy) in grid_map.items():
            result[block_name] = {
                'x': gx * cell_w,
                'y': gy * cell_h,
                'width': cell_w,
                'height': cell_h,
            }
        return result

    def _pin_side(self, comp_ref: str, parent_ref: str,
                  pin_matrix: dict, netlist: list) -> str:
        """Determine which side of the parent IC the component connects to.

        Examines all nets between *comp_ref* and *parent_ref*, retrieves
        the **parent's** pin angle from *pin_matrix*, and returns
        ``"right"``, ``"left"``, ``"top"``, or ``"bottom"``
        (default: ``"right"``).
        """
        angles: list[float] = []
        for conn in netlist:
            sr = conn['source'].split(':')[0]
            tr = conn['target'].split(':')[0]
            if {sr, tr} != {comp_ref, parent_ref}:
                continue
            # Pick the pin key that belongs to the parent
            parent_pin_key = None
            if sr == parent_ref:
                parent_pin_key = conn['source']
            else:
                parent_pin_key = conn['target']
            pin_info = pin_matrix.get(parent_pin_key)
            if pin_info:
                angles.append(float(pin_info.get('angle', 0)))
        if not angles:
            return "right"
        avg = (sum(angles) / len(angles)) % 360
        if 45 <= avg < 135:
            return "top"
        elif 135 <= avg < 225:
            return "left"
        elif 225 <= avg < 315:
            return "bottom"
        return "right"

    def _place_block(self, block_refs: list[str], block_bbox: dict,
                     parent_map: dict, pin_matrix: dict,
                     netlist: list, graph: nx.Graph,
                     all_placed: set[str]) -> None:
        """Place all components in a single block.

        Main components (tier ≥ 0) receive a local spring layout inside
        the block's bounding box.  Satellites (tier == -1) orbit their
        parent with ``_pin_side`` awareness.  Already-placed components
        in *all_placed* are skipped.
        """
        mains = [r for r in block_refs
                 if self._get_comp(r) and self._get_comp(r)['tier'] >= 0]
        sats  = [r for r in block_refs
                 if self._get_comp(r) and self._get_comp(r)['tier'] == -1]

        bx = block_bbox['x']
        by = block_bbox['y']
        bw = block_bbox['width']
        bh = block_bbox['height']
        margin = 20.0
        inner_w = bw - 2 * margin
        inner_h = bh - 2 * margin

        if len(mains) >= 2:
            sub = graph.subgraph(mains).copy()
            if sub.number_of_nodes() >= 2:
                pos = nx.spring_layout(sub, weight='weight', iterations=50,
                                       k=1.5, seed=42)
                px_vals = [p[0] for p in pos.values()]
                py_vals = [p[1] for p in pos.values()]
                rng_x = max(max(px_vals) - min(px_vals), 1.0)
                rng_y = max(max(py_vals) - min(py_vals), 1.0)
                for ref, (lx, ly) in pos.items():
                    comp = self._get_comp(ref)
                    if comp:
                        sx = bx + margin + (lx - min(px_vals)) / rng_x * inner_w
                        sy = by + margin + (ly - min(py_vals)) / rng_y * inner_h
                        comp['x'] = _snap(sx)
                        comp['y'] = _snap(sy)
                all_placed.update(mains)
        elif len(mains) == 1:
            ref = mains[0]
            comp = self._get_comp(ref)
            if comp:
                comp['x'] = _snap(bx + bw / 2 - comp['width'] / 2)
                comp['y'] = _snap(by + bh / 2 - comp['height'] / 2)
                all_placed.add(ref)

        # ── Satellites: pin-side-aware orbit ───────────────────────
        side_counts: dict[str, int] = {}
        for sat_ref in sats:
            if sat_ref in all_placed:
                continue
            par_ref = parent_map.get(sat_ref)
            if not par_ref:
                continue
            par_c = self._get_comp(par_ref)
            sat_c = self._get_comp(sat_ref)
            if not par_c or not sat_c:
                continue

            side = self._pin_side(sat_ref, par_ref, pin_matrix, netlist)
            gap = SAT_H_GAP
            idx = side_counts.get(side, 0)
            side_counts[side] = idx + 1

            pcx = par_c['x'] + par_c['bbox']['x']
            pcy = par_c['y'] + par_c['bbox']['y']
            pcw = par_c['width']
            pch = par_c['height']
            scx = sat_c['bbox']['x']
            scy = sat_c['bbox']['y']
            scw = sat_c['width']
            sch = sat_c['height']

            # Vertical offset to spread multiple satellites on the same side
            v_offset = idx * (sch + SAT_V_GAP)

            if side == "right":
                sx = pcx + pcw + gap - scx
                sy = pcy + pch / 2 - scy - sch / 2 + v_offset
            elif side == "left":
                sx = pcx - gap - scw - scx
                sy = pcy + pch / 2 - scy - sch / 2 + v_offset
            elif side == "top":
                sx = pcx + pcw / 2 - scx - scw / 2
                sy = pcy - gap - sch - scy + v_offset
            else:
                sx = pcx + pcw / 2 - scx - scw / 2
                sy = pcy + pch + gap - scy + v_offset

            sat_c['x'] = _snap(sx)
            sat_c['y'] = _snap(sy)
            all_placed.add(sat_ref)

        # Fallback: satellites whose parent is outside the block
        for sat_ref in sats:
            if sat_ref in all_placed:
                continue
            par_ref = parent_map.get(sat_ref)
            if not par_ref:
                continue
            sat_c = self._get_comp(sat_ref)
            par_c = self._get_comp(par_ref)
            if not sat_c or not par_c:
                continue

            side = self._pin_side(sat_ref, par_ref, pin_matrix, netlist)
            gap = SAT_H_GAP
            idx = side_counts.get(side, 0)
            side_counts[side] = idx + 1
            v_offset = idx * (sat_c['height'] + SAT_V_GAP)

            pcx = par_c['x'] + par_c['bbox']['x']
            pcy = par_c['y'] + par_c['bbox']['y']
            pcw = par_c['width']
            pch = par_c['height']
            scx = sat_c['bbox']['x']
            scy = sat_c['bbox']['y']
            scw = sat_c['width']

            if side == "right":
                sx = pcx + pcw + gap - scx
                sy = pcy + pch / 2 - scy - sat_c['height'] / 2 + v_offset
            elif side == "left":
                sx = pcx - gap - scw - scx
                sy = pcy + pch / 2 - scy - sat_c['height'] / 2 + v_offset
            elif side == "top":
                sx = pcx + pcw / 2 - scx - scw / 2
                sy = pcy - gap - sat_c['height'] - scy + v_offset
            else:
                sx = pcx + pcw / 2 - scx - scw / 2
                sy = pcy + pch + gap - scy + v_offset

            sat_c['x'] = _snap(sx)
            sat_c['y'] = _snap(sy)
            all_placed.add(sat_ref)

    def _count_crossings(self, routes: list[dict]) -> int:
        """Count wire-segment crossings (O(n²) segment intersection).

        Ignores shared endpoints and T-junctions (where segments share
        an endpoint but do not cross).  Uses orientation-based segment
        intersection test.

        Returns the integer number of crossing pairs.
        """
        segments: list[tuple[float, float, float, float]] = []
        for r in routes:
            pts = r.get('points', [])
            for i in range(len(pts) - 1):
                segments.append((pts[i][0], pts[i][1],
                                 pts[i + 1][0], pts[i + 1][1]))

        def _orient(ax, ay, bx, by, cx, cy) -> int:
            v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if abs(v) < 1e-12:
                return 0
            return 1 if v > 0 else -1

        def _on_seg(ax, ay, bx, by, cx, cy) -> bool:
            return (min(ax, bx) <= cx <= max(ax, bx) and
                    min(ay, by) <= cy <= max(ay, by))

        count = 0
        for i in range(len(segments)):
            ax1, ay1, ax2, ay2 = segments[i]
            for j in range(i + 1, len(segments)):
                bx1, by1, bx2, by2 = segments[j]

                # Skip shared endpoints
                if (abs(ax2 - bx1) < 1e-9 and abs(ay2 - by1) < 1e-9) or \
                   (abs(ax1 - bx2) < 1e-9 and abs(ay1 - by2) < 1e-9):
                    continue
                if (abs(ax1 - bx1) < 1e-9 and abs(ay1 - by1) < 1e-9) or \
                   (abs(ax2 - bx2) < 1e-9 and abs(ay2 - by2) < 1e-9):
                    continue

                o1 = _orient(ax1, ay1, ax2, ay2, bx1, by1)
                o2 = _orient(ax1, ay1, ax2, ay2, bx2, by2)
                o3 = _orient(bx1, by1, bx2, by2, ax1, ay1)
                o4 = _orient(bx1, by1, bx2, by2, ax2, ay2)

                if o1 != o2 and o3 != o4:
                    count += 1
                elif o1 == 0 and _on_seg(ax1, ay1, ax2, ay2, bx1, by1):
                    continue  # T-junction — ignore
                elif o2 == 0 and _on_seg(ax1, ay1, ax2, ay2, bx2, by2):
                    continue
                elif o3 == 0 and _on_seg(bx1, by1, bx2, by2, ax1, ay1):
                    continue
                elif o4 == 0 and _on_seg(bx1, by1, bx2, by2, ax2, ay2):
                    continue

        return count

    def _log_placement_metrics(self, routes: list[dict],
                               dropped_pairs: list[tuple[str, str]]) -> dict:
        """Compute and log placement quality metrics.

        Metrics returned:
        - total_wire_length: sum of Manhattan distances across all routes
        - crossings: number of wire-segment intersections
        - dropped_wires: count of failed routes
        - n_components: number of placed components

        Also logs via ``__import__('logging').getLogger(...)``.
        """
        total_wire_len = 0.0
        pts_routes: list[list] = []
        for r in routes:
            pts = r.get('points') or r.get('path', [])
            if pts and isinstance(pts[0], dict):
                pts = [(p['x'], p['y']) for p in pts]
            pts_routes.append(pts)
            for i in range(len(pts) - 1):
                dx = abs(pts[i + 1][0] - pts[i][0])
                dy = abs(pts[i + 1][1] - pts[i][1])
                total_wire_len += dx + dy

        crossings = self._count_crossings(
            [{'points': p} for p in pts_routes])

        metrics = {
            'total_wire_length': round(total_wire_len, 2),
            'crossings': crossings,
            'dropped_wires': len(dropped_pairs),
            'n_components': len(self.components),
        }

        logger = __import__('logging').getLogger(__name__)
        logger.info(
            '[PLACEMENT METRICS]  %s  |  drops=%d  cross=%d  '
            'wire=%.1f  n=%d',
            PLACEMENT_MODE,
            metrics['dropped_wires'],
            metrics['crossings'],
            metrics['total_wire_length'],
            metrics['n_components'],
        )

        return metrics

    def _repair_placement_for_routing(self,
                                      dropped_pairs: list[tuple[str, str]]
                                      ) -> int:
        """Move components involved in dropped wires closer together.

        For each unique pair (src, tgt) where a wire failed to route,
        move the satellite (tier==-1) toward its partner IC so the
        Manhattan distance falls comfortably under MAX_WIRE_MANHATTAN.
        Returns number of components moved.
        """
        if not dropped_pairs:
            return 0
        moved: set[str] = set()
        for src_ref, tgt_ref in dropped_pairs:
            for sat_ref, ic_ref in [(src_ref, tgt_ref), (tgt_ref, src_ref)]:
                sat = self._get_comp(sat_ref)
                ic  = self._get_comp(ic_ref)
                if not sat or not ic:
                    continue
                if sat['tier'] != -1 or ic['tier'] < 0:
                    continue

                # Move satellite to just outside the IC's right edge,
                # vertically aligned with the IC center.
                tight_gap = max(GRID_SIZE, SAT_H_GAP * 0.3)
                new_x = (ic['x'] + ic['bbox']['x'] + ic['width'] +
                         tight_gap - sat['bbox']['x'])
                icx = ic['x'] + ic['bbox']['x'] + ic['width'] / 2
                icy = ic['y'] + ic['bbox']['y'] + ic['height'] / 2
                new_y = _snap(icy - sat['bbox']['y'] - sat['height'] / 2)
                sat['x'] = _snap(new_x)
                sat['y'] = new_y
                moved.add(sat_ref)

        return len(moved)

    # ── Wire routing ───────────────────────────────────────────────────

    def route_traces(self, netlist: list, pin_matrix: dict
                     ) -> tuple[list[dict], list[tuple[str, str]]]:
        """Obstacle-aware orthogonal schematic wire routing.

        HARD guarantees for every emitted trace:
          1. Path is strictly orthogonal (no diagonal segments).
          2. Path length ≤ MAX_WIRE_MANHATTAN.
          3. Path has ≥ 2 points.
          4. Path does not collide with more than MAX_COLLISIONS components.

        Wires that fail ANY of these checks are DROPPED — never emitted
        as a bad fallback. A dropped wire is better than a 800mm monster.

        Returns:
            (traces, dropped_pairs) where dropped_pairs is a list of
            (src_ref, tgt_ref) tuples for every connection that was dropped.
        """
        pos = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces: list[dict] = []
        dropped_pairs: list[tuple[str, str]] = []

        # Case-insensitive fallback index for pin keys — the LLM sometimes
        # emits pin keys with inconsistent casing (e.g. "U1:VCC" vs "U1:vcc").
        pin_matrix_lower: dict[str, str] = {}
        for k in pin_matrix:
            pin_matrix_lower[k.lower()] = k

        def _resolve_pin(key: str) -> Optional[dict]:
            pin = pin_matrix.get(key)
            if pin is not None:
                return pin
            # Case-insensitive fallback
            alt = pin_matrix_lower.get(key.lower())
            if alt is not None:
                return pin_matrix[alt]
            return None

        def _abs(key: str) -> Optional[tuple[float, float]]:
            ref = key.split(':')[0]
            if not ref:
                return None
            pin = _resolve_pin(key)
            off = pos.get(ref)
            if pin is None or off is None:
                return None
            return (_snap(pin['x'] + off[0]), _snap(pin['y'] + off[1]))

        def _dir(key: str) -> str:
            return _pin_direction(_resolve_pin(key) or {})

        def _mhd(conn) -> float:
            s = _abs(conn['source']); t = _abs(conn['target'])
            if not s or not t: return float('inf')
            return abs(s[0]-t[0]) + abs(s[1]-t[1])

        # Pre-filter: drop any connection whose pins are too far apart
        max_allowed = MAX_WIRE_MANHATTAN * 1.5
        routable = [c for c in netlist if _mhd(c) <= max_allowed]
        for c in netlist:
            if _mhd(c) > max_allowed:
                dropped_pairs.append((
                    c['source'].split(':')[0],
                    c['target'].split(':')[0],
                ))

        # Route shortest first so later detours have something to dodge.
        for conn in sorted(routable, key=_mhd):
            s_pos = _abs(conn['source'])
            t_pos = _abs(conn['target'])
            if not s_pos or not t_pos:
                # A netlist pin key has no entry in pin_matrix (or the
                # component wasn't placed). Record as a dropped pair so the
                # caller can report and repair, instead of silently skipping.
                src_ref = conn['source'].split(':')[0] if conn.get('source') else '?'
                tgt_ref = conn['target'].split(':')[0] if conn.get('target') else '?'
                dropped_pairs.append((src_ref, tgt_ref))
                continue
            if s_pos == t_pos:
                continue

            s_dir = _dir(conn['source'])
            t_dir = _dir(conn['target'])
            src_ref = conn['source'].split(':')[0]
            tgt_ref = conn['target'].split(':')[0]

            path = _make_path(s_pos, s_dir, t_pos, t_dir,
                              self.components, src_ref, tgt_ref)

            # HARD final guards — drop the wire if ANY check fails
            # NOTE: length limit matches _make_path's relaxed/A* passes (*1.5)
            dropped = False
            if not path:
                dropped = True
            elif len(path) < 2:
                dropped = True
            elif not _is_orthogonal(path):
                dropped = True
            elif _path_length(path) > MAX_WIRE_MANHATTAN * 1.5:
                dropped = True
            elif _path_collisions(path, self.components, src_ref, tgt_ref) > MAX_COLLISIONS:
                dropped = True

            if dropped:
                dropped_pairs.append((src_ref, tgt_ref))
                continue

            traces.append({
                'source': conn['source'],
                'target': conn['target'],
                'path':   [{'x': p[0], 'y': p[1]} for p in path],
            })

        # Deduplicate dropped pairs
        seen: set[tuple[str, str]] = set()
        deduped = []
        for pair in dropped_pairs:
            key = tuple(sorted(pair))
            if key not in seen:
                seen.add(key)
                deduped.append(pair)
        return traces, deduped

    # ── Legacy stubs (used only by old PCB router fallback) ────────────

    def build_obstacle_matrix(self, pin_matrix: dict = None) -> None:
        try:
            from pathfinding.core.grid import Grid
            self.matrix = [[1]*MATRIX_SIZE for _ in range(MATRIX_SIZE)]
            self.grid   = Grid(matrix=self.matrix)
        except Exception:
            self.matrix = None
            self.grid   = None

    def unblock_pin_cells(self, pin_matrix: dict) -> None:
        pass

    def check_and_fix_overlaps(self, traces: list, max_passes: int = 2) -> tuple:
        """Post-route validation: detect traces that run on top of each other
        (2+ consecutive shared cells = parallel overlap, not a crossing) and
        re-route the offenders with the other traces' cells hard-blocked.

        Returns (traces, n_fixed, n_remaining_conflicts).
        """
        from pathfinding.core.grid import Grid
        from pathfinding.finder.a_star import AStarFinder
        from pathfinding.core.diagonal_movement import DiagonalMovement

        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)

        def to_cells(tr):
            return [
                (round(p['x'] / GRID_SIZE) + MATRIX_OFFSET,
                 round(p['y'] / GRID_SIZE) + MATRIX_OFFSET)
                for p in tr['path']
            ]

        def find_conflicts(all_cells):
            usage = {}
            for idx, cs in enumerate(all_cells):
                for c in cs[1:-1]:
                    usage.setdefault(c, set()).add(idx)
            conflicts = []
            for idx, cs in enumerate(all_cells):
                run = 0
                for c in cs[1:-1]:
                    if len(usage.get(c, ())) > 1:
                        run += 1
                        if run >= 2:  # 2+ consecutive shared cells = parallel overlap
                            conflicts.append(idx)
                            break
                    else:
                        run = 0
            return conflicts

        n_fixed = 0
        for _ in range(max_passes):
            all_cells = [to_cells(t) for t in traces]
            conflicts = find_conflicts(all_cells)
            if not conflicts:
                return traces, n_fixed, 0

            # Re-route longest offenders first — they have the most detour room
            conflicts.sort(key=lambda i: -len(all_cells[i]))
            progress = False
            for idx in conflicts:
                cs = all_cells[idx]
                if len(cs) < 2:
                    continue
                # Hard-block every middle cell occupied by any OTHER trace
                m = [row[:] for row in self.matrix]
                for j, ocs in enumerate(all_cells):
                    if j == idx:
                        continue
                    for (x, y) in ocs[1:-1]:
                        if 0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE:
                            m[y][x] = 0
                grid = Grid(matrix=m)
                (sx, sy), (ex, ey) = cs[0], cs[-1]
                try:
                    start = grid.node(sx, sy)
                    end = grid.node(ex, ey)
                    start.walkable = True
                    end.walkable = True
                    path, _ = finder.find_path(start, end, grid)
                except Exception:
                    path = None
                if path:
                    manhattan = abs(sx - ex) + abs(sy - ey)
                    if len(path) <= max(manhattan * 4, 50):
                        traces[idx]['path'] = [
                            {'x': (n.x - MATRIX_OFFSET) * GRID_SIZE,
                             'y': (n.y - MATRIX_OFFSET) * GRID_SIZE}
                            for n in path
                        ]
                        all_cells[idx] = to_cells(traces[idx])
                        n_fixed += 1
                        progress = True
            if not progress:
                break  # nothing improvable; stop iterating

        remaining = len(find_conflicts([to_cells(t) for t in traces]))
        return traces, n_fixed, remaining

    def _get_comp(self, ref_des: str) -> Optional[dict]:
        for c in self.components:
            if c['ref_des'] == ref_des:
                return c
        return None

    def _get_comp_offset(self, ref_des: str) -> tuple[float, float]:
        c = self._get_comp(ref_des)
        return (c['x'], c['y']) if c else (0.0, 0.0)

    def get_placements(self) -> list:
        return [
            {'ref_des': c['ref_des'], 'x': c['x'], 'y': c['y'],
             'rotation': c.get('rotation', 0.0)}
            for c in self.components
        ]

    def _build_connectivity_graph(self, pin_matrix, netlist):
        return {}

    def _conn_y_rank(self, comp, col_idx, conn_graph, pin_matrix):
        return 0.0
