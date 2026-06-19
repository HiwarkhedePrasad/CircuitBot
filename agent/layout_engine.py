"""Backend layout and routing engine.

Runs column-based component placement and A* orthogonal wire routing
entirely in Python. The frontend receives pre-computed absolute coordinates
and wire paths — no spatial math on the client.
"""

import math
from pathfinding.core.grid import Grid

GRID_SIZE = 1.27  # 50 mil KiCad standard
MATRIX_SIZE = 300
MATRIX_OFFSET = 150  # offset to keep grid coords positive

BBOX_PAD = 2.0
COLUMN_SPACING = 20.32  # extra horizontal routing channel between columns (16 grid cells)
ROW_CLEARANCE = 6.35    # extra vertical routing channel between rows (5 grid cells)

# Column definitions — must match frontend COLUMN_DEFS
COLUMN_KEYWORDS = [
    ['REGULATOR', 'CONNECTOR', 'POWER', 'BATTERY', 'SWITCH', 'FUSE', 'DIODE', 'POLYFUSE'],
    ['LDO', 'BUCK', 'BOOST', 'CAPACITOR', 'RESISTOR', 'INDUCTOR', 'FILTER', 'CONVERTER'],
    ['MCU', 'ESP32', 'STM32', 'PROCESSOR', 'FPGA', 'DSP', 'MEMORY', 'CPU', 'RF_MODULE'],
    [],  # default
]

_IDSTR_TYPE_MAP = {
    'C_SMALL': 'CAPACITOR', 'C_SMALL_US': 'CAPACITOR', 'C_POLARIZED': 'CAPACITOR',
    'R_SMALL': 'RESISTOR', 'R': 'RESISTOR',
    'POLYFUSE': 'FUSE', 'LED': 'DIODE',
}


def _get_column_for_category(category: str, id_str: str = '') -> int:
    cat = category.upper()
    id_name = id_str.split(':')[-1].upper() if ':' in id_str else id_str.upper()
    mapped = _IDSTR_TYPE_MAP.get(id_name, '')
    text = f'{cat} {mapped}'
    for i, keywords in enumerate(COLUMN_KEYWORDS):
        for kw in keywords:
            if kw in text:
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

    def add_component(self, ref_des: str, ops: list, category: str, id_str: str = ''):
        bbox = calculate_ops_bbox(ops)
        self.components.append({
            'ref_des': ref_des,
            'ops': ops,
            'category': category,
            'id_str': id_str,
            'bbox': bbox,
            'x': 0.0,
            'y': 0.0,
            'width': bbox['w'],
            'height': bbox['h'],
        })

    def set_component_position(self, ref_des: str, x: float, y: float, rotation: float = 0):
        """Set the (x, y, rotation) of a previously added component."""
        for c in self.components:
            if c['ref_des'] == ref_des:
                c['x'] = x
                c['y'] = y
                c['rotation'] = rotation
                return

    def execute_placement(self, pin_matrix: dict = None, netlist: list = None):
        """Column-based auto-layout with connectivity-aware vertical ordering."""
        if not self.components:
            return

        cols = [[], [], [], []]
        for comp in self.components:
            comp['column'] = _get_column_for_category(comp['category'], comp.get('id_str', ''))
            cols[comp['column']].append(comp)

        # ── Connectivity-aware ordering within each column ──
        # Compute a connectivity score: pair of components that share a net
        # should be placed near each other vertically.
        if pin_matrix and netlist:
            conn_graph = self._build_connectivity_graph(pin_matrix, netlist)
            for col_idx, col in enumerate(cols):
                if len(col) < 2:
                    continue
                # Sort by the average Y of connected pins in adjacent columns
                col.sort(key=lambda c: self._conn_y_rank(c, col_idx, conn_graph, pin_matrix))

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

    def _build_connectivity_graph(self, pin_matrix: dict, netlist: list) -> dict:
        """Build a map: (ref_a, ref_b) -> list of connection Y-coord pairs."""
        conn = {}
        comp_pos = {c['ref_des']: c for c in self.components}
        for net in netlist:
            src_ref = net['source'].split(':')[0]
            tgt_ref = net['target'].split(':')[0]
            if src_ref == tgt_ref:
                continue
            key = (src_ref, tgt_ref) if src_ref < tgt_ref else (tgt_ref, src_ref)
            src_pin = pin_matrix.get(net['source'])
            tgt_pin = pin_matrix.get(net['target'])
            if src_pin and tgt_pin:
                src_comp = comp_pos.get(src_ref)
                tgt_comp = comp_pos.get(tgt_ref)
                if src_comp and tgt_comp:
                    # Estimate Y in world coords (before placement, use bbox center)
                    sy = src_pin['y']  # relative to comp origin
                    ty = tgt_pin['y']
                    conn.setdefault(key, []).append((sy, ty))
        return conn

    def _conn_y_rank(self, comp: dict, col_idx: int, conn_graph: dict,
                     pin_matrix: dict) -> float:
        """Compute a rank value for component ordering within a column.
        Lower values = place higher (more negative Y).
        Looks at the average Y of connected pins on components in adjacent columns.
        """
        ref = comp['ref_des']
        connected_ys = []
        for (a, b), pairs in conn_graph.items():
            if a == ref:
                partner = b
            elif b == ref:
                partner = a
            else:
                continue
            # Only consider connections to adjacent columns
            partner_comp = self._get_comp(partner)
            if not partner_comp:
                continue
            partner_col = partner_comp.get('column', 3)
            if abs(partner_col - col_idx) > 1:
                continue
            for _, ty in pairs:
                connected_ys.append(ty if a == ref else ty)
        if not connected_ys:
            return 0.0
        return sum(connected_ys) / len(connected_ys)

    def build_obstacle_matrix(self, pin_matrix: dict = None):
        """Build a walkable grid with component footprints blocked.

        Blocks the FULL inflated bounding box of every component (covers
        polyline-drawn bodies too), then carves narrow escape corridors
        outward from each pin so wires can exit but cannot cut through
        the component body.
        """
        matrix = [[1 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]

        # 1) Block full inflated bbox of every component
        for comp in self.components:
            bx = comp['x'] + comp['bbox']['x']
            by = comp['y'] + comp['bbox']['y']
            gsx = math.floor(bx / GRID_SIZE) + MATRIX_OFFSET - 1
            gsy = math.floor(by / GRID_SIZE) + MATRIX_OFFSET - 1
            gex = math.ceil((bx + comp['bbox']['w']) / GRID_SIZE) + MATRIX_OFFSET + 1
            gey = math.ceil((by + comp['bbox']['h']) / GRID_SIZE) + MATRIX_OFFSET + 1
            for gx in range(gsx, gex + 1):
                for gy in range(gsy, gey + 1):
                    if 0 <= gx < MATRIX_SIZE and 0 <= gy < MATRIX_SIZE:
                        matrix[gy][gx] = 0

        # 2) Carve a 1-cell escape corridor outward from each pin endpoint
        if pin_matrix:
            for key, pin in pin_matrix.items():
                ref = key.split(':')[0]
                comp = self._get_comp(ref)
                if not comp:
                    continue
                px = pin['x'] + comp['x']
                py = pin['y'] + comp['y']
                gpx = round(px / GRID_SIZE) + MATRIX_OFFSET
                gpy = round(py / GRID_SIZE) + MATRIX_OFFSET
                if not (0 <= gpx < MATRIX_SIZE and 0 <= gpy < MATRIX_SIZE):
                    continue

                # Outward direction: away from component bbox center (dominant axis)
                ccx = comp['x'] + comp['bbox']['x'] + comp['bbox']['w'] / 2
                ccy = comp['y'] + comp['bbox']['y'] + comp['bbox']['h'] / 2
                dx = px - ccx
                dy = py - ccy
                if abs(dx) >= abs(dy):
                    step = (1 if dx >= 0 else -1, 0)
                else:
                    step = (0, 1 if dy >= 0 else -1)

                # Carve from pin cell outward until clear of the blocked region (+2 cells)
                cx_g, cy_g = gpx, gpy
                cleared = 0
                for _ in range(60):
                    if 0 <= cx_g < MATRIX_SIZE and 0 <= cy_g < MATRIX_SIZE:
                        if matrix[cy_g][cx_g] == 1:
                            cleared += 1
                        matrix[cy_g][cx_g] = 1
                    if cleared >= 5:
                        break
                    cx_g += step[0]
                    cy_g += step[1]

        self.matrix = matrix
        self.grid = Grid(matrix=matrix)

    def _get_comp(self, ref_des: str):
        for c in self.components:
            if c['ref_des'] == ref_des:
                return c
        return None

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
        """Orthogonal L/Z-shaped schematic wire routing.

        Replaces the A* maze router. Schematics allow wire crossings — no
        obstacle avoidance needed. Simple 2-4-point Manhattan paths produce
        clean, readable output without the giant ghost loops A* creates.
        """
        EXIT_STUB = GRID_SIZE * 3
        comp_positions = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces = []

        def _net_len(conn):
            s = pin_matrix.get(conn['source'])
            t = pin_matrix.get(conn['target'])
            if not s or not t:
                return float('inf')
            sr = conn['source'].split(':')[0]
            tr = conn['target'].split(':')[0]
            so = comp_positions.get(sr)
            to_ = comp_positions.get(tr)
            if so is None or to_ is None:
                return float('inf')
            return abs((s['x'] + so[0]) - (t['x'] + to_[0])) + abs((s['y'] + so[1]) - (t['y'] + to_[1]))

        ordered = sorted(netlist, key=_net_len)

        for conn in ordered:
            src_key = conn['source']
            tgt_key = conn['target']
            src = pin_matrix.get(src_key)
            tgt = pin_matrix.get(tgt_key)
            if not src or not tgt:
                continue

            src_ref = src_key.split(':')[0]
            tgt_ref = tgt_key.split(':')[0]

            if not src_ref or not tgt_ref:
                continue

            src_off = comp_positions.get(src_ref)
            tgt_off = comp_positions.get(tgt_ref)
            if src_off is None or tgt_off is None:
                continue

            sx = _snap(src['x'] + src_off[0])
            sy = _snap(src['y'] + src_off[1])
            ex = _snap(tgt['x'] + tgt_off[0])
            ey = _snap(tgt['y'] + tgt_off[1])

            if sx == ex and sy == ey:
                continue

            if sx == ex:
                path = [{'x': sx, 'y': sy}, {'x': ex, 'y': ey}]
            elif sy == ey:
                path = [{'x': sx, 'y': sy}, {'x': ex, 'y': ey}]
            else:
                pin_dir = src.get('direction', 'right')
                stub_dx = -EXIT_STUB if pin_dir == 'left' else EXIT_STUB
                mid_x = _snap(sx + stub_dx)
                if (stub_dx > 0 and mid_x > ex) or (stub_dx < 0 and mid_x < ex):
                    mid_x = _snap((sx + ex) / 2)
                path = [
                    {'x': sx,    'y': sy},
                    {'x': mid_x, 'y': sy},
                    {'x': mid_x, 'y': ey},
                    {'x': ex,    'y': ey},
                ]

            traces.append({
                'source': src_key,
                'target': tgt_key,
                'path': path,
            })

        return traces

    def check_and_fix_overlaps(self, traces: list, max_passes: int = 2):
        """Post-route validation: detect traces that run on top of each other
        (2+ consecutive shared cells = parallel overlap, not a crossing) and
        re-route the offenders with the other traces' cells hard-blocked.

        Returns (traces, n_fixed, n_remaining_conflicts).
        """
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

    def get_placements(self) -> list:
        """Return final absolute positions for all components."""
        return [
            {'ref_des': c['ref_des'], 'x': c['x'], 'y': c['y'],
             'rotation': c.get('rotation', 0)}
            for c in self.components
        ]
