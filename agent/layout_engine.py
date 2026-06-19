"""
agent/layout_engine.py  —  Hierarchical schematic layout + Z-shaped routing.

Two-phase layout:
  Phase 1 – Tier placement (signal-flow left→right).
             Satellites (caps, resistors) placed adjacent to their parent IC.
  Phase 2 – Z-shaped orthogonal wire routing.  No A*, no obstacle avoidance.
             Hard wire-length cap prevents ghost loops.
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
PIN_STUB_LEN   = 3.81

MAX_WIRE_MANHATTAN = 180.0

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
    'ATmega':         'MCU',
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
    for key, typ in _IDSTR_HINTS.items():
        if key.upper() in id_name.upper():
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

    for op in ops:
        t = op[0]
        if t == 'rectangle':
            s = _get_attr(op, 'start'); e = _get_attr(op, 'end')
            if s: upd(float(s[1]), float(s[2]))
            if e: upd(float(e[1]), float(e[2]))
        elif t == 'polyline':
            pts = _get_attr(op, 'pts')
            if pts:
                for i in range(1, len(pts)):
                    if pts[i][0] == 'xy': upd(float(pts[i][1]), float(pts[i][2]))
        elif t == 'circle':
            c = _get_attr(op, 'center'); r = _get_attr(op, 'radius')
            if c and r:
                cx, cy, rv = float(c[1]), float(c[2]), float(r[1])
                upd(cx-rv, cy-rv); upd(cx+rv, cy+rv)
        elif t == 'pin':
            a = _get_attr(op, 'at')
            if a: upd(float(a[1]), float(a[2]))
        elif t in ('property', 'text'):
            a = _get_attr(op, 'at'); h = _get_attr(op, 'hide')
            if a and (not h or h[1] != 'yes'):
                x, y = float(a[1]), float(a[2])
                txt = op[1][1] if len(op) > 1 and isinstance(op[1], list) else ''
                upd(x, y); upd(x + len(txt) * 1.27, y - 2.54)

    if mn_x == float('inf'):
        return {'x': -5.0, 'y': -5.0, 'w': 10.0, 'h': 10.0}
    return {
        'x': mn_x - BBOX_PAD,
        'y': mn_y - BBOX_PAD,
        'w': mx_x - mn_x + BBOX_PAD * 2,
        'h': mx_y - mn_y + BBOX_PAD * 2,
    }


class BackendLayoutEngine:
    """Hierarchical schematic placement + Z-shaped orthogonal wire routing."""

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
        """Hierarchical tier placement with satellite passives."""
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
            y_cursor = 0.0
            for c in comps:
                c['x'] = _snap(x_cursor + (tier_w - c['width']) / 2)
                c['y'] = _snap(y_cursor - c['bbox']['y'])
                y_cursor += c['height'] + BBOX_PAD * 2 + COMP_V_GAP
            x_cursor += tier_w + TIER_GAP

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
            sy = _snap(par_c['y'])
            for i, s in enumerate(group):
                s['x'] = sx
                s['y'] = _snap(sy + i * (s['height'] + SAT_V_GAP))

        if orphan_sats:
            rx = max((c['x'] + c['width'] for c in self.components
                      if c['tier'] != -1), default=x_cursor)
            rx = _snap(rx + TIER_GAP)
            ry = 0.0
            for s in orphan_sats:
                s['x'] = rx
                s['y'] = _snap(ry)
                ry += s['height'] + SAT_V_GAP

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
        """Z-shaped orthogonal schematic wire routing with ghost-loop guard."""
        pos = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces = []

        def _abs(key: str) -> Optional[tuple[float, float]]:
            ref = key.split(':')[0]
            if not ref:
                return None
            pin = pin_matrix.get(key)
            off = pos.get(ref)
            if pin is None or off is None:
                return None
            return (_snap(pin['x'] + off[0]), _snap(pin['y'] + off[1]))

        def _mhd(conn) -> float:
            s = _abs(conn['source']); t = _abs(conn['target'])
            if not s or not t: return float('inf')
            return abs(s[0]-t[0]) + abs(s[1]-t[1])

        for conn in sorted(netlist, key=_mhd):
            s_pos = _abs(conn['source'])
            t_pos = _abs(conn['target'])
            if not s_pos or not t_pos:
                continue

            sx, sy = s_pos
            ex, ey = t_pos

            if abs(ex-sx) + abs(ey-sy) > MAX_WIRE_MANHATTAN:
                continue
            if sx == ex and sy == ey:
                continue

            if abs(sx - ex) < 0.001:
                path = [{'x': sx, 'y': sy}, {'x': ex, 'y': ey}]
            elif abs(sy - ey) < 0.001:
                path = [{'x': sx, 'y': sy}, {'x': ex, 'y': ey}]
            else:
                src_pin = pin_matrix.get(conn['source'], {})
                pin_dir = src_pin.get('direction', 'right')
                stub_dx = -PIN_STUB_LEN if pin_dir == 'left' else PIN_STUB_LEN
                mid_x = _snap(sx + stub_dx)
                if stub_dx > 0 and mid_x > ex - PIN_STUB_LEN:
                    mid_x = _snap((sx + ex) / 2)
                elif stub_dx < 0 and mid_x < ex + PIN_STUB_LEN:
                    mid_x = _snap((sx + ex) / 2)
                path = [
                    {'x': sx,    'y': sy},
                    {'x': mid_x, 'y': sy},
                    {'x': mid_x, 'y': ey},
                    {'x': ex,    'y': ey},
                ]

            traces.append({
                'source': conn['source'],
                'target': conn['target'],
                'path':   path,
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

    def check_and_fix_overlaps(self, traces: list,
                               max_passes: int = 2) -> tuple:
        return traces, 0, 0

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
