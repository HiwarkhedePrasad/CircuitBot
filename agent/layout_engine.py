"""Backend layout and routing engine.

Runs column-based component placement and A* orthogonal wire routing
entirely in Python. The frontend receives pre-computed absolute coordinates
and wire paths — no spatial math on the client.
"""

import math
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder
from pathfinding.core.diagonal_movement import DiagonalMovement

GRID_SIZE = 1.27  # 50 mil KiCad standard
MATRIX_SIZE = 300
MATRIX_OFFSET = 150  # offset to keep grid coords positive

BBOX_PAD = 2.0
COLUMN_SPACING = 15.0
ROW_CLEARANCE = 3.0

# Column definitions — must match frontend COLUMN_DEFS
COLUMN_KEYWORDS = [
    ['REGULATOR', 'CONNECTOR', 'POWER', 'BATTERY', 'SWITCH', 'FUSE', 'DIODE'],
    ['LDO', 'BUCK', 'BOOST', 'CAPACITOR', 'INDUCTOR', 'FILTER', 'CONVERTER'],
    ['MCU', 'ESP32', 'STM32', 'PROCESSOR', 'FPGA', 'DSP', 'MEMORY', 'CPU', 'RF_MODULE'],
    [],  # default
]


def _get_column_for_category(category: str) -> int:
    cat = category.upper()
    for i, keywords in enumerate(COLUMN_KEYWORDS):
        for kw in keywords:
            if kw in cat:
                return i
    return 3


def _snap(value: float) -> float:
    return round(value / GRID_SIZE) * GRID_SIZE


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


def calculate_ops_bbox(ops: list) -> dict:
    """Calculate bounding box of a component's drawing ops.
    Mirrors the frontend calculateOpsBBox() logic.
    """
    min_x, min_y = float('inf'), float('inf')
    max_x, max_y = float('-inf'), float('-inf')

    def upd(x, y):
        nonlocal min_x, min_y, max_x, max_y
        if x < min_x: min_x = x
        if x > max_x: max_x = x
        if y < min_y: min_y = y
        if y > max_y: max_y = y

    for op in ops:
        typ = op[0]
        if typ == 'rectangle':
            s = _get_attr(op, 'start')
            e = _get_attr(op, 'end')
            if s: upd(float(s[1]), float(s[2]))
            if e: upd(float(e[1]), float(e[2]))
        elif typ == 'polyline':
            pts = _get_attr(op, 'pts')
            if pts:
                for i in range(1, len(pts)):
                    if pts[i][0] == 'xy':
                        upd(float(pts[i][1]), float(pts[i][2]))
        elif typ == 'circle':
            c = _get_attr(op, 'center')
            r = _get_attr(op, 'radius')
            if c and r:
                cx, cy = float(c[1]), float(c[2])
                rv = float(r[1])
                upd(cx - rv, cy - rv)
                upd(cx + rv, cy + rv)
        elif typ == 'pin':
            at = _get_attr(op, 'at')
            if at:
                upd(float(at[1]), float(at[2]))
        elif typ in ('property', 'text'):
            at = _get_attr(op, 'at')
            hide = _get_attr(op, 'hide')
            if at and (not hide or hide[1] != 'yes'):
                x, y = float(at[1]), float(at[2])
                # Text renders rightward/downward from anchor; estimate extent
                text_content = op[1][1] if len(op) > 1 and isinstance(op[1], list) and len(op[1]) > 1 else ''
                text_width = len(text_content) * 1.27  # ~1.27mm per char
                upd(x, y)
                upd(x + text_width, y - 2.54)

    if min_x == float('inf'):
        return {'x': -5, 'y': -5, 'w': 10, 'h': 10}
    return {
        'x': min_x - BBOX_PAD,
        'y': min_y - BBOX_PAD,
        'w': max_x - min_x + BBOX_PAD * 2,
        'h': max_y - min_y + BBOX_PAD * 2,
    }


class BackendLayoutEngine:
    """Handles component placement and A* wire routing on the backend."""

    def __init__(self):
        self.components = []  # list of dicts with ref_des, ops, category, bbox, x, y

    def add_component(self, ref_des: str, ops: list, category: str):
        bbox = calculate_ops_bbox(ops)
        self.components.append({
            'ref_des': ref_des,
            'ops': ops,
            'category': category,
            'bbox': bbox,
            'x': 0.0,
            'y': 0.0,
            'width': bbox['w'],
            'height': bbox['h'],
        })

    def execute_placement(self):
        """Column-based auto-layout matching frontend behavior."""
        if not self.components:
            return

        cols = [[], [], [], []]
        for comp in self.components:
            comp['column'] = _get_column_for_category(comp['category'])
            cols[comp['column']].append(comp)

        col_widths = []
        for col in cols:
            if not col:
                col_widths.append(0)
            else:
                col_widths.append(max(c['width'] + BBOX_PAD * 2 for c in col))

        active = sum(1 for c in cols if c)
        total_width = sum(col_widths) + (active - 1) * COLUMN_SPACING
        x_offset = -total_width / 2

        for col_idx, col in enumerate(cols):
            if not col:
                continue

            col_total = sum(c['height'] + BBOX_PAD * 2 for c in col) - BBOX_PAD * 2 + ROW_CLEARANCE * (len(col) - 1)
            y_offset = -col_total / 2

            for comp in col:
                comp['x'] = _snap(x_offset + (col_widths[col_idx] - comp['width']) / 2)
                comp['y'] = _snap(y_offset - comp['bbox']['y'])
                y_offset += comp['height'] + BBOX_PAD * 2 + ROW_CLEARANCE

            x_offset += col_widths[col_idx] + COLUMN_SPACING

    def build_obstacle_matrix(self, pin_matrix: dict = None):
        """Build a walkable grid with component footprints blocked.
        
        Blocks individual drawing shapes (rect, circle) but carves out
        corridors for pin connection points so wires can exit.
        """
        matrix = [[1 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]

        # Pre-compute pin grid positions to carve corridors
        carve_set = set()
        if pin_matrix:
            for key, pin in pin_matrix.items():
                ref = key.split(':')[0]
                off = self._get_comp_offset(ref)
                px = math.floor((pin['x'] + off[0]) / GRID_SIZE) + MATRIX_OFFSET
                py = math.floor((pin['y'] + off[1]) / GRID_SIZE) + MATRIX_OFFSET
                for dx in range(-3, 4):
                    for dy in range(-3, 4):
                        carve_set.add((px + dx, py + dy))

        for comp in self.components:
            abs_comp_x = comp['x']
            abs_comp_y = comp['y']

            for op in comp['ops']:
                typ = op[0]
                if typ == 'rectangle':
                    s = self._get_attr(op, 'start')
                    e = self._get_attr(op, 'end')
                    if s and e:
                        x1 = float(s[1])
                        y1 = float(s[2])
                        x2 = float(e[1])
                        y2 = float(e[2])
                        gsx = math.floor((abs_comp_x + min(x1, x2)) / GRID_SIZE) + MATRIX_OFFSET
                        gsy = math.floor((abs_comp_y + min(y1, y2)) / GRID_SIZE) + MATRIX_OFFSET
                        gex = math.ceil((abs_comp_x + max(x1, x2)) / GRID_SIZE) + MATRIX_OFFSET
                        gey = math.ceil((abs_comp_y + max(y1, y2)) / GRID_SIZE) + MATRIX_OFFSET
                        for gx in range(gsx - 2, gex + 3):
                            for gy in range(gsy - 2, gey + 3):
                                if 0 <= gx < MATRIX_SIZE and 0 <= gy < MATRIX_SIZE:
                                    if (gx, gy) not in carve_set:
                                        matrix[gy][gx] = 0
                elif typ == 'circle':
                    c = self._get_attr(op, 'center')
                    r = self._get_attr(op, 'radius')
                    if c and r:
                        cx = abs_comp_x + float(c[1])
                        cy = abs_comp_y + float(c[2])
                        rv = float(r[1])
                        cx_g = round(cx / GRID_SIZE) + MATRIX_OFFSET
                        cy_g = round(cy / GRID_SIZE) + MATRIX_OFFSET
                        r_g = math.ceil(rv / GRID_SIZE) + 3  # 2-cell clearance padding
                        for gx in range(max(0, cx_g - r_g), min(MATRIX_SIZE, cx_g + r_g + 1)):
                            for gy in range(max(0, cy_g - r_g), min(MATRIX_SIZE, cy_g + r_g + 1)):
                                if (gx - cx_g) ** 2 + (gy - cy_g) ** 2 <= r_g ** 2:
                                    if (gx, gy) not in carve_set:
                                        matrix[gy][gx] = 0

        self.grid = Grid(matrix=matrix)

    def _get_comp_offset(self, ref_des: str):
        for c in self.components:
            if c['ref_des'] == ref_des:
                return (c['x'], c['y'])
        return (0, 0)

    def unblock_pin_cells(self, pin_matrix: dict):
        """(Deprecated) Pin corridors are now carved during build_obstacle_matrix."""
        pass

    def _get_attr(self, node, name):
        """Get attribute sub-list from a KiCad S-expr node."""
        if not isinstance(node, list):
            return None
        for child in node[1:]:
            if isinstance(child, list) and child[0] == name:
                return child
        return None

    def route_traces(self, netlist: list, pin_matrix: dict) -> list:
        """A* orthogonal routing for each net in the netlist."""
        comp_positions = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces = []
        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)

        for conn in netlist:
            src = pin_matrix.get(conn['source'])
            tgt = pin_matrix.get(conn['target'])
            if not src or not tgt:
                continue

            src_ref = conn['source'].split(':')[0]
            tgt_ref = conn['target'].split(':')[0]
            src_off = comp_positions.get(src_ref, (0, 0))
            tgt_off = comp_positions.get(tgt_ref, (0, 0))

            sx = math.floor((src['x'] + src_off[0]) / GRID_SIZE) + MATRIX_OFFSET
            sy = math.floor((src['y'] + src_off[1]) / GRID_SIZE) + MATRIX_OFFSET
            ex = math.floor((tgt['x'] + tgt_off[0]) / GRID_SIZE) + MATRIX_OFFSET
            ey = math.floor((tgt['y'] + tgt_off[1]) / GRID_SIZE) + MATRIX_OFFSET

            if not (0 <= sx < MATRIX_SIZE and 0 <= sy < MATRIX_SIZE and
                    0 <= ex < MATRIX_SIZE and 0 <= ey < MATRIX_SIZE):
                continue

            self.grid.cleanup()

            start = self.grid.node(sx, sy)
            end = self.grid.node(ex, ey)
            # Safety: ensure pin cells are walkable even if carve-out missed them
            start.walkable = True
            end.walkable = True
            path, _ = finder.find_path(start, end, self.grid)

            if path:
                mm_path = [
                    {'x': (n.x - MATRIX_OFFSET) * GRID_SIZE,
                     'y': (n.y - MATRIX_OFFSET) * GRID_SIZE}
                    for n in path
                ]
                traces.append({
                    'source': conn['source'],
                    'target': conn['target'],
                    'path': mm_path,
                })

        return traces

    def get_placements(self) -> list:
        """Return final absolute positions for all components."""
        return [
            {'ref_des': c['ref_des'], 'x': c['x'], 'y': c['y']}
            for c in self.components
        ]
