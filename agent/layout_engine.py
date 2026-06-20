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
import re
from typing import Optional

GRID_SIZE  = 1.27
BBOX_PAD   = 1.5

TIER_GAP       = 20.32
COMP_V_GAP     = 7.62
SAT_H_GAP      = 12.70
SAT_V_GAP      = 3.81
PIN_STUB_LEN   = 2.54          # one grid step out from the symbol body

MAX_WIRE_MANHATTAN = 150.0     # HARD cap — anything longer is DROPPED
MAX_COMPS_PER_COLUMN = 4       # grid placement: max components per column
MAX_COLLISIONS = 0             # STRICT: wires must NOT pass through any component body
BBOX_CLEARANCE = 1.27          # extra clearance margin around symbol bodies (1 grid step)

MATRIX_SIZE   = 300
MATRIX_OFFSET = 150
COLUMN_SPACING = 20.32
ROW_CLEARANCE  = 6.35


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
    ('LDO',        1), ('REGULATOR', 1), ('BUCK',    1),
    ('BOOST',      1), ('CONVERTER', 1),
    ('MCU',        2), ('PROCESSOR', 2), ('ESP32',   2),
    ('STM32',      2), ('FPGA',      2), ('CPU',     2),
    ('RF_MODULE',  2), ('DSP',       2), ('MEMORY',  2),
    ('SENSOR',     3), ('DISPLAY',   3), ('DRIVER',  3),
    ('INDICATOR',  3), ('LED',       3),
    ('ESD_IC',     0), ('DIODE',     0), ('ZENER',   0),
    ('CAPACITOR',  -1), ('RESISTOR', -1),
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

    For the source and target components, we only ignore collisions on
    the first and last segments of the path respectively, preventing wires
    from routing through their own components.
    """
    if len(path) < 2:
        return 0
    hits = 0
    for i in range(len(path) - 1):
        p1, p2 = path[i], path[i + 1]
        if abs(p1[0] - p2[0]) < 1e-3 and abs(p1[1] - p2[1]) < 1e-3:
            continue
        for c in components:
            ref = c['ref_des']
            # Allow the first segment (connection to source pin) to touch/intersect the source component
            if ref == src_ref and i == 0:
                continue
            # Allow the last segment (connection to target pin) to touch/intersect the target component
            if ref == tgt_ref and i == len(path) - 2:
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


def _make_path(s_pos, s_dir, t_pos, t_dir, components, src_ref, tgt_ref):
    """Generate, score, and pick the best orthogonal path.

    Returns None if no candidate satisfies the length cap and collision
    limit — caller must DROP the wire in that case (do NOT fallback).
    """
    s_stub = _stub_point(*s_pos, s_dir)
    t_stub = _stub_point(*t_pos, t_dir)

    candidates = []
    candidates += _candidate_straight(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_L(s_pos, s_stub, t_pos, t_stub)
    candidates += _candidate_Z(s_pos, s_stub, t_pos, t_stub, components)

    best_path = None
    best_score = float('inf')
    for raw in candidates:
        path = _clean_path(raw)
        if len(path) < 2:
            continue
        # HARD length cap — drop candidate, do not fallback
        length = _path_length(path)
        if length > MAX_WIRE_MANHATTAN:
            continue
        # Collision cap — drop candidate if it hits ANY component body
        collisions = _path_collisions(path, components, src_ref, tgt_ref)
        if collisions > MAX_COLLISIONS:
            continue
        bends = _bend_count(path)
        # Score: zero-collision paths first, then shortest, then fewest bends
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

    def add_component(self, ref_des: str, ops: list, category: str,
                      id_str: str = '') -> None:
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

        tiers: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
        sats:  list[dict] = []
        for c in self.components:
            if c['tier'] == -1:
                sats.append(c)
            else:
                tiers.setdefault(c['tier'], []).append(c)

        for t in tiers:
            tiers[t].sort(key=lambda c: -c['height'])

        x_cursor = 0.0
        for tier_idx in sorted(tiers):
            comps = tiers[tier_idx]
            if not comps:
                continue
            tier_w = max(c['width'] for c in comps) + BBOX_PAD * 2

            col_count = max(1, math.ceil(len(comps) / MAX_COMPS_PER_COLUMN))
            col_w = tier_w + TIER_GAP

            # Group components by column
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
            sx = _snap(par_c['x'] + par_c['width'] + SAT_H_GAP)
            sy_start = _snap(par_c['y'])
            
            sat_col_count = max(1, math.ceil(len(group) / MAX_COMPS_PER_COLUMN))
            sat_cols = [[] for _ in range(sat_col_count)]
            for i, s in enumerate(group):
                col_idx = i // MAX_COMPS_PER_COLUMN
                sat_cols[col_idx].append(s)
                
            for col_idx, col_sats in enumerate(sat_cols):
                y_cursor = sy_start
                for s in col_sats:
                    s['x'] = _snap(sx + col_idx * (s['width'] + SAT_H_GAP))
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

        # Centre everything around (0, 0)
        xs = [c['x'] for c in self.components]
        ys = [c['y'] for c in self.components]
        if xs:
            ox = _snap((max(xs) + min(xs)) / 2)
            oy = _snap((max(ys) + min(ys)) / 2)
            for c in self.components:
                c['x'] = _snap(c['x'] - ox)
                c['y'] = _snap(c['y'] - oy)

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

    # ── Wire routing ───────────────────────────────────────────────────

    def route_traces(self, netlist: list, pin_matrix: dict) -> list:
        """Obstacle-aware orthogonal schematic wire routing.

        HARD guarantees for every emitted trace:
          1. Path is strictly orthogonal (no diagonal segments).
          2. Path length ≤ MAX_WIRE_MANHATTAN.
          3. Path has ≥ 2 points.
          4. Path does not collide with more than MAX_COLLISIONS components.

        Wires that fail ANY of these checks are DROPPED — never emitted
        as a bad fallback. A dropped wire is better than a 800mm monster.
        """
        pos = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces = []
        n_dropped = 0

        def _abs(key: str) -> Optional[tuple[float, float]]:
            ref = key.split(':')[0]
            if not ref:
                return None
            pin = pin_matrix.get(key)
            off = pos.get(ref)
            if pin is None or off is None:
                return None
            return (_snap(pin['x'] + off[0]), _snap(pin['y'] + off[1]))

        def _dir(key: str) -> str:
            return _pin_direction(pin_matrix.get(key, {}))

        def _mhd(conn) -> float:
            s = _abs(conn['source']); t = _abs(conn['target'])
            if not s or not t: return float('inf')
            return abs(s[0]-t[0]) + abs(s[1]-t[1])

        # Pre-filter: drop any connection whose pins are too far apart
        routable = [c for c in netlist if _mhd(c) <= MAX_WIRE_MANHATTAN]
        n_pre_filtered = len(netlist) - len(routable)

        # Route shortest first so later detours have something to dodge.
        for conn in sorted(routable, key=_mhd):
            s_pos = _abs(conn['source'])
            t_pos = _abs(conn['target'])
            if not s_pos or not t_pos:
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
            if not path:
                n_dropped += 1
                continue
            if len(path) < 2:
                n_dropped += 1
                continue
            if not _is_orthogonal(path):
                n_dropped += 1
                continue
            if _path_length(path) > MAX_WIRE_MANHATTAN:
                n_dropped += 1
                continue
            if _path_collisions(path, self.components, src_ref, tgt_ref) > MAX_COLLISIONS:
                n_dropped += 1
                continue

            traces.append({
                'source': conn['source'],
                'target': conn['target'],
                'path':   [{'x': p[0], 'y': p[1]} for p in path],
            })

        return traces

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
