"""
agent/layout_engine.py  —  LAYOUT ENGINE FACADE

This module is now a thin facade. All placement logic lives under
``agent/placement/`` and all routing logic under ``agent/routing/``.

Kept for backward compatibility:
    - ``BackendLayoutEngine`` class (delegates to placement/routing packages)
    - All constants (GRID_SIZE, MAX_WIRE_MANHATTAN, …)
    - All ``_NET_CLASSES`` / ``_TIER_RULES`` / ``_IDSTR_HINTS`` tables
    - ``_snap``, ``calculate_ops_bbox``, ``_sem_type``, ``_tier``
"""

from __future__ import annotations

from typing import Optional

import networkx as nx

# ── Constants (kept in source for immediate visibility) ──────────────────

GRID_SIZE  = 1.27
BBOX_PAD   = 1.5

TIER_GAP       = 20.32
COMP_V_GAP     = 7.62
SAT_H_GAP      = 12.70
SAT_V_GAP      = 3.81
PIN_STUB_LEN   = 2.54

MAX_WIRE_MANHATTAN = 250.0
MAX_COMPS_PER_COLUMN = 4
MAX_COLLISIONS = 0
BBOX_CLEARANCE = 0.635
MAX_SAT_DISTANCE = 30.0
MAX_WIRE_PT2PT = 350.0

MATRIX_SIZE   = 300
MATRIX_OFFSET = 150
COLUMN_SPACING = 20.32
ROW_CLEARANCE  = 6.35

PLACEMENT_MODE = "blocks_v2"
OVERLAP_PULLBACK = 0.30
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
    "CRYSTAL": 5.0, "USB": 4.0, "SPI": 3.0, "UART": 2.5,
    "I2C": 2.0, "POWER": 1.5, "RESET": 1.5,
}

_PAIR_CONSTRAINTS: dict[tuple[str, str], float] = {
    ("MCU", "CRYSTAL"): 10.0, ("MCU", "CAPACITOR"): 8.0,
    ("LDO", "CAPACITOR"): 7.0, ("USB", "ESD_IC"): 8.0,
    ("MCU", "SENSOR"): 6.0, ("MCU", "RF_MODULE"): 5.0,
    ("REGULATOR", "CAPACITOR"): 7.0, ("MCU", "RESISTOR"): 3.0,
    ("SENSOR", "RESISTOR"): 4.0, ("RF_MODULE", "CAPACITOR"): 4.0,
}

_TIER_RULES: list[tuple[str, int]] = [
    ("CONNECTOR", 0), ("USB", 0), ("BATTERY", 0),
    ("FUSE", 0), ("POLYFUSE", 0), ("SWITCH", 0),
    ("SPST", 0), ("SPDT", 0), ("TACTILE", 0),
    ("PUSHBUTTON", 0), ("DIP", 0), ("ROCKER", 0),
    ("TOGGLE", 0), ("SLIDE", 0),
    ("LDO", 1), ("REGULATOR", 1), ("BUCK", 1),
    ("BOOST", 1), ("CONVERTER", 1),
    ("MCU", 2), ("PROCESSOR", 2), ("ESP32", 2),
    ("STM32", 2), ("FPGA", 2), ("CPU", 2),
    ("RF_MODULE", 2), ("DSP", 2), ("MEMORY", 2),
    ("SENSOR", 3), ("DISPLAY", 3), ("DRIVER", 3),
    ("INDICATOR", 3),
    ("ESD_IC", 0), ("DIODE", 0), ("ZENER", 0),
    ("SW_", -1), ("SWITCH", 0), ("BUTTON", -1),
    ("LED", -1), ("CAPACITOR", -1), ("RESISTOR", -1),
]

_IDSTR_HINTS: dict[str, str] = {
    "C_Small": "CAPACITOR", "C_Small_US": "CAPACITOR",
    "C_Polarized": "CAPACITOR",
    "R_Small": "RESISTOR", "R": "RESISTOR",
    "Polyfuse": "FUSE", "LED": "LED",
    "D_Small": "DIODE", "Zener": "ZENER",
    "ATmega": "MCU", "ATtiny": "MCU", "AT90": "MCU",
    "ATxmega": "MCU", "AVR128DA": "MCU", "AVR128DB": "MCU",
    "AVR64DA": "MCU", "AVR64DD": "MCU",
    "AMS1117": "LDO", "DS18B20": "SENSOR",
    "TPD6S300A": "ESD_IC", "USBLC6": "ESD_IC",
    "OLED": "DISPLAY", "SSD1306": "DISPLAY",
}

# Re-export block-detection constants for tests
from agent.placement.community import _BLOCK_SEEDS, _BLOCK_ROLE  # noqa: E402, F401


# ── Module-level helpers ─────────────────────────────────────────────────

def _snap(v: float) -> float:
    return round(v / GRID_SIZE) * GRID_SIZE


def _sem_type(category: str, id_str: str = '') -> str:
    id_name = id_str.split(':')[-1] if ':' in id_str else id_str
    id_up = id_name.upper()
    for key, typ in _IDSTR_HINTS.items():
        key_up = key.upper()
        if key_up == "R":
            if id_up == "R" or id_up.startswith("R_") or id_up.startswith("R-"):
                return typ
        elif key_up == "LED":
            if "OLED" not in id_up and "LED" in id_up:
                return typ
        elif key_up in id_up:
            return typ
    return category.upper().replace(" ", "_")


def _tier(category: str, id_str: str = '') -> int:
    sem = _sem_type(category, id_str)
    for kw, t in _TIER_RULES:
        if kw in sem:
            return t
    id_name = id_str.split(':')[-1] if ':' in id_str else id_str
    id_up = id_name.upper().replace(" ", "_")
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
    mn_x = mn_y = float('inf')
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
        if t == "rectangle":
            has_graphics = True
            s = _get_attr(op, "start"); e = _get_attr(op, "end")
            if s: upd(float(s[1]), float(s[2]))
            if e: upd(float(e[1]), float(e[2]))
        elif t == "polyline":
            has_graphics = True
            pts = _get_attr(op, "pts")
            if pts:
                for i in range(1, len(pts)):
                    if pts[i][0] == "xy":
                        upd(float(pts[i][1]), float(pts[i][2]))
        elif t == "circle":
            has_graphics = True
            c = _get_attr(op, "center"); r = _get_attr(op, "radius")
            if c and r:
                cx, cy, rv = float(c[1]), float(c[2]), float(r[1])
                upd(cx - rv, cy - rv)
                upd(cx + rv, cy + rv)
        elif t == "arc":
            has_graphics = True
            for name in ("start", "mid", "end"):
                point = _get_attr(op, name)
                if point:
                    upd(float(point[1]), float(point[2]))

    # Pins are visible geometry and must be part of the routing keep-out even
    # when a symbol body also exists.
    for op in ops:
        if op[0] == "pin":
            a = _get_attr(op, "at")
            length = _get_attr(op, "length")
            if a:
                x, y = float(a[1]), float(a[2])
                upd(x, y)
                if length:
                    import math
                    angle = math.radians(float(a[3]) if len(a) > 3 else 0.0)
                    pin_len = float(length[1])
                    upd(x + math.cos(angle) * pin_len,
                        y + math.sin(angle) * pin_len)

    if mn_x == float("inf"):
        return {"x": -5.0, "y": -5.0, "w": 10.0, "h": 10.0}
    return {
        "x": mn_x - BBOX_PAD,
        "y": mn_y - BBOX_PAD,
        "w": mx_x - mn_x + BBOX_PAD * 2,
        "h": mx_y - mn_y + BBOX_PAD * 2,
    }


# ── Re-export routing helpers for backward compat ────────────────────────

from agent.routing.geometry import _pin_direction, _stub_point, _seg_intersects_bbox  # noqa: E402, F401
from agent.routing.collision import _path_collisions  # noqa: E402, F401
from agent.routing.path_utils import _path_length, _bend_count, _clean_path, _is_orthogonal  # noqa: E402, F401
from agent.routing.candidates import _candidate_straight, _candidate_L, _candidate_Z, _candidate_U  # noqa: E402, F401
from agent.routing.astar import _astar_orthogonal  # noqa: E402, F401
from agent.routing.make_path import make_path as _make_path  # noqa: E402, F401


# ── Layout engine (facade) ───────────────────────────────────────────────


class BackendLayoutEngine:
    """Hierarchical schematic placement + obstacle-aware orthogonal routing.

    This is now a thin facade — the heavy lifting lives in
    ``agent/placement/`` and ``agent/routing/``.
    """

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

    def execute_placement(self, pin_matrix: dict = None,
                          netlist: list = None) -> None:
        if not self.components:
            return

        from agent.placement import PlacementEngine as _PE
        eng = _PE.create()
        placements = eng.place(self.components, netlist or [], pin_matrix or {})

        pmap = {p['ref_des']: p for p in placements}
        for c in self.components:
            p = pmap.get(c['ref_des'])
            if p:
                c['x'] = p['x']
                c['y'] = p['y']
                c['rotation'] = p.get('rotation', 0.0)

        # Block map (for schematic_layout logging)
        try:
            from agent.placement.blocks_v2 import _build_weighted_graph
            from agent.placement.community import detect_blocks
            graph = _build_weighted_graph(self.components, netlist or [], pin_matrix or {})
            self._last_block_map = detect_blocks(graph, netlist or [])
        except Exception:
            self._last_block_map = None

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
                if sat_c.get('tier', -1) == -1 and ic_c.get('tier', -1) >= 0:
                    key = (sat_c['ref_des'], ic_c['ref_des'])
                    scores[key] = scores.get(key, 0) + 1
        parent: dict[str, str] = {}
        for (sat, ic), _ in sorted(scores.items(), key=lambda kv: -kv[1]):
            if sat not in parent:
                parent[sat] = ic
        return parent

    def _enforce_satellite_distance(self, parent_map: dict) -> int:
        moved = 0
        for c in self.components:
            if c.get('tier', -1) != -1:
                continue
            par_ref = parent_map.get(c['ref_des'])
            if not par_ref:
                continue
            par = self._get_comp(par_ref)
            if not par:
                continue
            from agent.placement.blocks_v2 import _snap as _snp, SAT_H_GAP
            from agent.routing.constants import GRID_SIZE as _GS
            pcx = par['x'] + par['bbox']['x'] + par['width'] / 2
            pcy = par['y'] + par['bbox']['y'] + par['height'] / 2
            scx = c['x'] + c['bbox']['x'] + c['width'] / 2
            scy = c['y'] + c['bbox']['y'] + c['height'] / 2
            dist = abs(pcx - scx) + abs(pcy - scy)
            if dist > MAX_SAT_DISTANCE:
                gap = max(_GS, SAT_H_GAP * 0.3)
                c['x'] = _snp(par['x'] + par['bbox']['x'] + par['width'] +
                              gap - c['bbox']['x'])
                c['y'] = _snp(pcy - c['bbox']['y'] - c['height'] / 2)
                moved += 1
        return moved

    def _remove_overlaps(self, max_iters: int = 100) -> int:
        from agent.placement.blocks_v2 import _remove_overlaps as _ro
        return _ro(self.components, max_iters)

    def _build_weighted_graph(self, netlist: list,
                              pin_matrix: dict) -> nx.Graph:
        from agent.placement.blocks_v2 import _build_weighted_graph as _bwg
        return _bwg(self.components, netlist, pin_matrix)

    def _detect_blocks_louvain(self, graph: nx.Graph,
                               netlist: list) -> dict[str, str]:
        from agent.placement.community import detect_blocks
        return detect_blocks(graph, netlist)

    def _seed_block_assignments(self, netlist: list) -> dict[str, str]:
        from agent.placement.community import seed_block_assignments
        return seed_block_assignments(netlist)

    def _block_grid_layout(self, blocks: dict[str, list[str]]) -> dict[str, dict]:
        from agent.placement.blocks_v2 import _block_grid_layout as _bgl
        return _bgl(blocks)

    def _pin_side(self, comp_ref: str, parent_ref: str,
                  pin_matrix: dict, netlist: list) -> str:
        from agent.placement.blocks_v2 import _pin_side as _ps
        return _ps(comp_ref, parent_ref, pin_matrix, netlist)

    def _place_block(self, block_refs: list[str], block_bbox: dict,
                     parent_map: dict, pin_matrix: dict,
                     netlist: list, graph: nx.Graph,
                     all_placed: set[str]) -> None:
        from agent.placement.blocks_v2 import _place_block as _pb
        _pb(block_refs, block_bbox, parent_map, pin_matrix,
            netlist, graph, all_placed, self.components)

    def _count_crossings(self, routes: list[dict]) -> int:
        from agent.routing.api import count_crossings
        return count_crossings(routes)

    def _log_placement_metrics(self, routes: list[dict],
                               dropped_pairs: list[tuple[str, str]]) -> dict:
        from agent.routing.api import log_placement_metrics
        return log_placement_metrics(self.components, routes, dropped_pairs)

    def _repair_placement_for_routing(self,
                                      dropped_pairs: list[tuple[str, str]]) -> int:
        from agent.routing.api import repair_placement_for_routing
        return repair_placement_for_routing(self.components, dropped_pairs)

    def route_traces(self, netlist: list, pin_matrix: dict
                     ) -> tuple[list[dict], list[tuple[str, str]]]:
        from agent.routing.api import route_traces as _rt
        return _rt(self.components, netlist, pin_matrix)

    # ── Legacy stubs (used only by old PCB router fallback) ────────────

    def build_obstacle_matrix(self, pin_matrix: dict = None) -> None:
        try:
            from pathfinding.core.grid import Grid
            self.matrix = [[1] * MATRIX_SIZE for _ in range(MATRIX_SIZE)]
            self.grid = Grid(matrix=self.matrix)
        except Exception:
            self.matrix = None
            self.grid = None

    def unblock_pin_cells(self, pin_matrix: dict) -> None:
        pass

    def check_and_fix_overlaps(self, traces: list, max_passes: int = 2) -> tuple:
        from agent.routing.api import check_and_fix_overlaps as _cfo
        return _cfo(traces, self.components, self.matrix, max_passes)

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
