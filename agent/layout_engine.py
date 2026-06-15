"""Backend layout and routing engine.

Runs column-based component placement and A* orthogonal wire routing
entirely in Python. The frontend receives pre-computed absolute coordinates
and wire paths — no spatial math on the client.

v3 — Obstacle-aware fallback, higher turn cost, centrality-first ordering,
     post-route collision validation, relaxed ghost rejection.
"""

import math
import heapq
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder
from pathfinding.core.diagonal_movement import DiagonalMovement

GRID_SIZE = 1.27  # 50 mil KiCad standard
MATRIX_SIZE = 600
MATRIX_OFFSET = 300  # offset to keep grid coords positive

BBOX_PAD = 2.0
COLUMN_SPACING = 6.35   # extra horizontal routing channel between columns (5 grid cells)
ROW_CLEARANCE = 3.81    # extra vertical routing channel between rows (3 grid cells)

# Cell values in the routing matrix
CELL_FREE = 1           # Walkable
CELL_BLOCKED = 0        # Component body or hard obstacle
CELL_TRACE = -1         # Occupied by a routed wire (hard-block for subsequent routes)
CELL_PIN_CORRIDOR = 2   # Pin escape corridor (walkable, but trace-aware)

# Column definitions — must match frontend COLUMN_DEFS
COLUMN_KEYWORDS = [
    ['REGULATOR', 'CONNECTOR', 'POWER', 'BATTERY', 'SWITCH', 'FUSE', 'DIODE'],
    ['LDO', 'BUCK', 'BOOST', 'CAPACITOR', 'INDUCTOR', 'FILTER', 'CONVERTER'],
    ['MCU', 'ESP32', 'STM32', 'PROCESSOR', 'FPGA', 'DSP', 'MEMORY', 'CPU', 'RF_MODULE'],
    [],  # default
]

# Maximum A* path length as a multiple of Manhattan distance.
# Prevents absurdly long ghost wires while allowing reasonable detours.
MAX_PATH_RATIO = 10
# Absolute minimum path length that is always accepted (in grid cells).
# Below this threshold we never reject — short connections have high detour ratios.
MIN_PATH_ABSOLUTE = 30
# Turn cost for weighted A* — penalizes zig-zag routing
TURN_COST = 6


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
    bbox = {
        'x': min_x - BBOX_PAD,
        'y': min_y - BBOX_PAD,
        'w': max_x - min_x + BBOX_PAD * 2,
        'h': max_y - min_y + BBOX_PAD * 2,
    }
    return _enforce_pin_density_minimum(bbox, ops)


def calculate_geom_bbox(ops: list) -> dict:
    """Calculate bounding box of ONLY the physical geometry and pins.
    EXCLUDES property and text labels to prevent massive invisible walls.
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
        elif typ == 'arc':
            for key in ('start', 'mid', 'end'):
                a = _get_attr(op, key)
                if a: upd(float(a[1]), float(a[2]))
        elif typ == 'pin':
            at = _get_attr(op, 'at')
            len_node = _get_attr(op, 'length')
            if at and len_node:
                x, y = float(at[1]), float(at[2])
                l = float(len_node[1])
                ang = float(at[3] or 0) * 3.14159 / 180.0
                upd(x, y)
                upd(x + math.cos(ang) * l, y + math.sin(ang) * l)

    if min_x == float('inf'):
        return {'x': -2.54, 'y': -2.54, 'w': 5.08, 'h': 5.08}
    
    # Minimal padding for the core body
    PAD = 1.27
    return {
        'x': min_x - PAD,
        'y': min_y - PAD,
        'w': max_x - min_x + PAD * 2,
        'h': max_y - min_y + PAD * 2,
    }


LABEL_PITCH = 1.8  # mm per label row — matches the renderer's line spacing


def _enforce_pin_density_minimum(bbox: dict, ops: list) -> dict:
    """A geometry bbox can be 'correct' yet too small to fit one label row
    per pin on dense ICs. Enforce a minimum node size derived from per-side
    pin counts so layout always reserves enough room — generic for any pin
    count on any part, no per-component tuning."""
    side = {0: 0, 90: 0, 180: 0, 270: 0}
    for op in ops:
        if op[0] != 'pin':
            continue
        at = _get_attr(op, 'at')
        if not at:
            continue
        try:
            ang = float(at[3]) % 360 if len(at) > 3 else 0.0
        except (ValueError, IndexError):
            ang = 0.0
        bucket = min((0, 90, 180, 270),
                     key=lambda a: min(abs(ang - a), 360 - abs(ang - a)))
        side[bucket] += 1
    # Horizontal pins (0/180) stack vertically on the left/right sides;
    # vertical pins (90/270) stack horizontally on the top/bottom sides.
    min_h = max(side[0], side[180], 1) * LABEL_PITCH
    min_w = max(side[90], side[270], 1) * LABEL_PITCH
    if bbox['h'] < min_h:
        bbox['y'] -= (min_h - bbox['h']) / 2
        bbox['h'] = min_h
    if bbox['w'] < min_w:
        bbox['x'] -= (min_w - bbox['w']) / 2
        bbox['w'] = min_w
    return bbox


# ── Weighted A* implementation ─────────────────────────────────────────────

def _weighted_astar(matrix, start, end, matrix_size):
    """Weighted A* pathfinder that supports:
    - Hard-blocked cells (value 0): impassable
    - Free cells (value 1): normal cost
    - Trace-occupied cells (value CELL_TRACE = -1): impassable (hard-blocked)
    - Pin corridor cells (value CELL_PIN_CORRIDOR = 2): walkable with same cost as free
    - Turn cost: penalizes direction changes for cleaner routing

    Returns list of (x, y) grid coordinates, or None if no path found.
    """
    sx, sy = start
    ex, ey = end

    if not (0 <= sx < matrix_size and 0 <= sy < matrix_size and
            0 <= ex < matrix_size and 0 <= ey < matrix_size):
        return None

    # Directions: right, left, down, up
    DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1)]

    def heuristic(x, y):
        return abs(x - ex) + abs(y - ey)

    # Priority queue: (f_score, counter, x, y, prev_dx, prev_dy)
    counter = 0
    open_set = [(heuristic(sx, sy), counter, sx, sy, 0, 0)]
    came_from = {}
    g_score = {(sx, sy): 0}
    closed = set()

    while open_set:
        f, _, x, y, pdx, pdy = heapq.heappop(open_set)

        if (x, y) in closed:
            continue
        closed.add((x, y))

        if x == ex and y == ey:
            # Reconstruct path
            path = []
            node = (x, y)
            while node in came_from:
                path.append(node)
                node = came_from[node]
            path.append((sx, sy))
            path.reverse()
            return path

        for dx, dy in DIRS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < matrix_size and 0 <= ny < matrix_size):
                continue
            if (nx, ny) in closed:
                continue

            cell_val = matrix[ny][nx]
            # Hard-blocked: component body or occupied trace
            if cell_val == CELL_BLOCKED or cell_val == CELL_TRACE:
                continue

            # Base move cost
            move_cost = 1

            # Turn penalty: if direction changed from previous step
            if pdx != 0 or pdy != 0:
                if (dx, dy) != (pdx, pdy):
                    move_cost += TURN_COST

            new_g = g_score[(x, y)] + move_cost
            if new_g < g_score.get((nx, ny), float('inf')):
                g_score[(nx, ny)] = new_g
                came_from[(nx, ny)] = (x, y)
                counter += 1
                heapq.heappush(open_set, (new_g + heuristic(nx, ny), counter, nx, ny, dx, dy))

    return None


def _l_shaped_wire(sx, sy, ex, ey):
    """Generate a simple L-shaped fallback wire path in grid coordinates.
    Tries both horizontal-first and vertical-first, returns the shorter one.
    """
    step_x = 1 if ex >= sx else -1
    step_y = 1 if ey >= sy else -1
    path1 = [(x, sy) for x in range(sx, ex + step_x, step_x)]
    path1.extend((ex, y) for y in range(sy + step_y, ey + step_y, step_y))
    path2 = [(sx, y) for y in range(sy, ey + step_y, step_y)]
    path2.extend((x, ey) for x in range(sx + step_x, ex + step_x, step_x))
    return path1 if len(path1) <= len(path2) else path2


def _obstacle_aware_wire(sx, sy, ex, ey, matrix, matrix_size):
    """Smart fallback: try L, Z, and detour path variants, check against
    the obstacle matrix, and return the shortest collision-free path.

    Falls back to a simple L-shaped wire if no clean path is found.
    """
    def _build_path(corners):
        """Convert corner list into a continuous (x, y) path, deduping endpoints of adjacent segments."""
        path = []
        for i in range(len(corners) - 1):
            x1, y1 = corners[i]
            x2, y2 = corners[i + 1]
            if x1 == x2:
                step = 1 if y2 >= y1 else -1
                for y in range(y1, y2 + step, step):
                    pt = (x1, y)
                    if not path or pt != path[-1]:
                        path.append(pt)
            else:
                step = 1 if x2 >= x1 else -1
                for x in range(x1, x2 + step, step):
                    pt = (x, y1)
                    if not path or pt != path[-1]:
                        path.append(pt)
        return path

    def _is_clear(path):
        """Return True if the entire path avoids blocked and trace cells."""
        for x, y in path:
            if not (0 <= x < matrix_size and 0 <= y < matrix_size):
                return False
            if matrix[y][x] in (CELL_BLOCKED, CELL_TRACE):
                return False
        return True

    best, best_len = None, float('inf')

    # 1) L-shaped variants (2 segments)
    candidates = [
        [(sx, sy), (ex, sy), (ex, ey)],
        [(sx, sy), (sx, ey), (ex, ey)],
    ]

    # 2) Z-shaped variants (3 segments) — detour perpendicular then across
    dist = max(abs(ex - sx), abs(ey - sy), 4)
    offset = max(dist // 3, 2)
    if abs(ex - sx) > abs(ey - sy):
        # Horizontal dominant: detour vertically
        for sign in (1, -1):
            o = offset * sign
            candidates.append([(sx, sy), (sx, sy + o), (ex, sy + o), (ex, ey)])
            # Wider detour
            candidates.append([(sx, sy), (sx, sy + 2 * o), (ex, sy + 2 * o), (ex, ey)])
    else:
        # Vertical dominant: detour horizontally
        for sign in (1, -1):
            o = offset * sign
            candidates.append([(sx, sy), (sx + o, sy), (sx + o, ey), (ex, ey)])
            candidates.append([(sx, sy), (sx + 2 * o, sy), (sx + 2 * o, ey), (ex, ey)])

    for corners in candidates:
        p = _build_path(corners)
        if p and _is_clear(p) and len(p) < best_len:
            best, best_len = p, len(p)

    if best is not None:
        return best

    # Ultimate fallback: simple L (may overlap obstacles)
    return _l_shaped_wire(sx, sy, ex, ey)


class BackendLayoutEngine:
    """Handles component placement and A* wire routing on the backend."""

    def __init__(self):
        self.components = []  # list of dicts with ref_des, ops, category, bbox, x, y
        self._pin_mm_coords = {}  # ref_des -> list of (gx, gy) tuples for pin carve-out

    def add_component(self, ref_des: str, ops: list, category: str):
        bbox = calculate_ops_bbox(ops)
        geom_bbox = calculate_geom_bbox(ops)
        self.components.append({
            'ref_des': ref_des,
            'ops': ops,
            'category': category,
            'bbox': bbox,
            'geom_bbox': geom_bbox,
            'x': 0.0,
            'y': 0.0,
            'width': bbox['w'],
            'height': bbox['h'],
        })

    def execute_placement(self, pin_matrix: dict = None, netlist: list = None):
        """Graph-based placement using force-directed layout (spring model).

        Builds a connectivity graph from the netlist, runs spring layout,
        scales to canvas coordinates, and resolves overlaps.
        """
        if not self.components:
            return

        import networkx as nx

        # Build undirected graph from netlist
        G = nx.Graph()
        for comp in self.components:
            G.add_node(comp['ref_des'])

        if netlist:
            for conn in netlist:
                src = conn['source'].split(':')[0]
                tgt = conn['target'].split(':')[0]
                if src != tgt:
                    if G.has_edge(src, tgt):
                        G[src][tgt]['weight'] += 1
                    else:
                        G.add_edge(src, tgt, weight=1.0)

        print(f"\n" + "="*20 + " COMPONENT CONNECTIVITY GRAPH " + "="*20)
        nodes = list(G.nodes())
        for i, node in enumerate(nodes):
            connections = []
            for neighbor in G.neighbors(node):
                weight = G[node][neighbor].get('weight', 1)
                connections.append(f"{neighbor}(x{int(weight)})")
            print(f"  {node:<10} ───►  {', '.join(connections) or '(no signals)'}")
        print("="*64 + "\n")

        # Force-directed layout
        # k: Optimal distance between nodes. Increase if too crowded.
        pos = nx.spring_layout(G, k=3.5, iterations=200, weight='weight', seed=42)

        # Scale coordinates. Spring layout is roughly in [-1, 1].
        # Map to canvas area: Increase scale for more routing channels.
        scale = math.sqrt(len(self.components)) * 80.0

        for comp in self.components:
            ref = comp['ref_des']
            p = pos.get(ref, [0.0, 0.0])
            # Center the layout on 0,0
            comp['x'] = p[0] * scale
            comp['y'] = p[1] * scale

        # Push apart overlaps
        self._resolve_overlaps()

    def _resolve_overlaps(self, margin=10.16, max_iterations=60):
        """Iteratively push overlapping component bounding boxes apart.
        Uses geom_bbox (tight body) but adds a large margin for labels and routing.
        """
        for _ in range(max_iterations):
            moved = False
            for i in range(len(self.components)):
                for j in range(i + 1, len(self.components)):
                    c1 = self.components[i]
                    c2 = self.components[j]

                    # Use geom_bbox for stable resolution without label inflation
                    b1 = {
                        'x': c1['x'] + c1['geom_bbox']['x'],
                        'y': c1['y'] + c1['geom_bbox']['y'],
                        'w': c1['geom_bbox']['w'],
                        'h': c1['geom_bbox']['h']
                    }
                    b2 = {
                        'x': c2['x'] + c2['geom_bbox']['x'],
                        'y': c2['y'] + c2['geom_bbox']['y'],
                        'w': c2['geom_bbox']['w'],
                        'h': c2['geom_bbox']['h']
                    }

                    # Inflate with larger margin for labels and routing channels
                    b1['x'] -= margin/2; b1['y'] -= margin/2; b1['w'] += margin; b1['h'] += margin
                    b2['x'] -= margin/2; b2['y'] -= margin/2; b2['w'] += margin; b2['h'] += margin

                    # Check for overlap
                    overlap_x = min(b1['x'] + b1['w'], b2['x'] + b2['w']) - max(b1['x'], b2['x'])
                    overlap_y = min(b1['y'] + b1['h'], b2['y'] + b2['h']) - max(b1['y'], b2['y'])

                    if overlap_x > 0 and overlap_y > 0:
                        # Push apart along the axis of least overlap
                        dx = (b1['x'] + b1['w']/2) - (b2['x'] + b2['w']/2)
                        dy = (b1['y'] + b1['h']/2) - (b2['y'] + b2['h']/2)
                        
                        if overlap_x < overlap_y:
                            push = overlap_x / 2 + 0.5
                            c1['x'] += push if dx >= 0 else -push
                            c2['x'] -= push if dx >= 0 else -push
                        else:
                            push = overlap_y / 2 + 0.5
                            c1['y'] += push if dy >= 0 else -push
                            c2['y'] -= push if dy >= 0 else -push
                        moved = True
            if not moved:
                break

        # Snap all to grid after resolution
        for comp in self.components:
            comp['x'] = _snap(comp['x'])
            comp['y'] = _snap(comp['y'])


    def build_obstacle_matrix(self, pin_matrix: dict = None):
        """Build a walkable grid with component footprints blocked.

        Blocks the FULL inflated geom_bbox of every component (tight physical body),
        then carves 3-cell-wide escape corridors outward from each pin endpoint.
        """
        matrix = [[CELL_FREE for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]

        # 1) Block tight physical body (geom_bbox) + padding
        for comp in self.components:
            bx = comp['x'] + comp['geom_bbox']['x']
            by = comp['y'] + comp['geom_bbox']['y']
            # Add 1-cell padding (1.27mm) around the physical body
            gsx = math.floor((bx - 1.27) / GRID_SIZE) + MATRIX_OFFSET
            gsy = math.floor((by - 1.27) / GRID_SIZE) + MATRIX_OFFSET
            gex = math.ceil((bx + comp['geom_bbox']['w'] + 1.27) / GRID_SIZE) + MATRIX_OFFSET
            gey = math.ceil((by + comp['geom_bbox']['h'] + 1.27) / GRID_SIZE) + MATRIX_OFFSET
            for gx in range(gsx, gex + 1):
                for gy in range(gsy, gey + 1):
                    if 0 <= gx < MATRIX_SIZE and 0 <= gy < MATRIX_SIZE:
                        matrix[gy][gx] = CELL_BLOCKED

        # 2) Carve 3-cell-wide escape corridors outward from each pin endpoint
        carve_set = set()
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

                # Outward direction: away from component geom_bbox center
                ccx = comp['x'] + comp['geom_bbox']['x'] + comp['geom_bbox']['w'] / 2
                ccy = comp['y'] + comp['geom_bbox']['y'] + comp['geom_bbox']['h'] / 2
                dx = px - ccx
                dy = py - ccy
                if abs(dx) >= abs(dy):
                    step = (1 if dx >= 0 else -1, 0)
                    # Perpendicular direction for 3-cell width
                    perp = (0, 1)
                else:
                    step = (0, 1 if dy >= 0 else -1)
                    perp = (1, 0)

                # Carve a 3-cell-wide corridor from pin outward until clear
                # The corridor extends until 3 consecutive free cells in the center line
                cx_g, cy_g = gpx, gpy
                cleared = 0
                for _ in range(80):
                    if 0 <= cx_g < MATRIX_SIZE and 0 <= cy_g < MATRIX_SIZE:
                        was_blocked = (matrix[cy_g][cx_g] == CELL_BLOCKED)
                        matrix[cy_g][cx_g] = CELL_FREE
                        carve_set.add((cx_g, cy_g))

                        # Carve 3-cell width: mark perpendicular neighbors too
                        for pdir in [perp, (-perp[0], -perp[1])]:
                            nx = cx_g + pdir[0]
                            ny = cy_g + pdir[1]
                            if 0 <= nx < MATRIX_SIZE and 0 <= ny < MATRIX_SIZE:
                                if matrix[ny][nx] == CELL_BLOCKED:
                                    matrix[ny][nx] = CELL_FREE
                                    carve_set.add((nx, ny))

                        if not was_blocked:
                            cleared += 1
                        else:
                            cleared = 0  # Reset — still inside blocked region

                    if cleared >= 4:
                        break
                    cx_g += step[0]
                    cy_g += step[1]

        self.matrix = matrix
        self._original_matrix = [row[:] for row in matrix]  # pristine copy for overlap re-routing
        self._pin_carve_set = carve_set  # for overlap re-routing

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

    def _mark_trace_cells(self, path_cells):
        """Mark cells used by a successfully routed trace as hard-blocked
        so subsequent routes must go around them. This replaces the broken
        TRACE_WEIGHT approach that had no effect with the pathfinding library.
        """
        # Mark middle cells as trace-occupied (hard-block for future routes)
        # Keep first 2 and last 2 cells free so other nets can reach the same pins
        for x, y in path_cells[2:-2]:
            if 0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE:
                self.matrix[y][x] = CELL_TRACE

    def _all_trace_cells(self, traces):
        """Convert all trace paths from mm coords to grid cell lists."""
        result = []
        for tr in traces:
            result.append([
                (round(p['x'] / GRID_SIZE) + MATRIX_OFFSET,
                 round(p['y'] / GRID_SIZE) + MATRIX_OFFSET)
                for p in tr['path']
            ])
        return result

    def _validate_obstacle_collisions(self, traces, margin_mm=1.27):
        """Check all wire paths against component geom_bboxes.
        Returns indices of traces that pass through any component body.
        Skips the first/last 2 cells of each path (they are at/near pins).
        """
        offending = set()
        for i, tr in enumerate(traces):
            cells = [
                (round(p['x'] / GRID_SIZE) + MATRIX_OFFSET,
                 round(p['y'] / GRID_SIZE) + MATRIX_OFFSET)
                for p in tr['path']
            ]
            # Skip first 2 and last 2 cells (near pins)
            check_cells = cells[2:-2] if len(cells) > 4 else []
            for gx, gy in check_cells:
                # Convert grid cell to mm
                px = (gx - MATRIX_OFFSET) * GRID_SIZE
                py = (gy - MATRIX_OFFSET) * GRID_SIZE
                for comp in self.components:
                    bx = comp['x'] + comp['geom_bbox']['x']
                    by = comp['y'] + comp['geom_bbox']['y']
                    bw = comp['geom_bbox']['w']
                    bh = comp['geom_bbox']['h']
                    if (bx - margin_mm <= px <= bx + bw + margin_mm and
                        by - margin_mm <= py <= by + bh + margin_mm):
                        offending.add(i)
                        break
                if i in offending:
                    break
        return list(offending)

    def route_traces(self, netlist: list, pin_matrix: dict) -> list:
        """Weighted A* orthogonal routing for each net in the netlist.

        v3 improvements:
        1. Routes by connectivity centrality (most-connected components first)
           to keep critical routing channels open.
        2. Obstacle-aware fallback tries L/Z/detour variants against the matrix
           instead of drawing blindly through component bodies.
        3. Higher turn cost for cleaner, straighter wires with fewer segments.
        4. Post-route collision validation: re-routes any path that clips a
           component body.
        """
        comp_positions = {c['ref_des']: (c['x'], c['y']) for c in self.components}
        traces = []

        # Route by centrality: compute degree of each component, route
        # nets involving the most-connected components first so they get
        # first pick of routing channels.
        comp_degree = {}
        for conn in netlist:
            src_ref = conn['source'].split(':')[0]
            tgt_ref = conn['target'].split(':')[0]
            comp_degree[src_ref] = comp_degree.get(src_ref, 0) + 1
            comp_degree[tgt_ref] = comp_degree.get(tgt_ref, 0) + 1

        def _net_centrality(conn):
            src_ref = conn['source'].split(':')[0]
            tgt_ref = conn['target'].split(':')[0]
            return -max(comp_degree.get(src_ref, 0), comp_degree.get(tgt_ref, 0))

        ordered = sorted(netlist, key=_net_centrality)

        n_routed = 0
        n_fallback = 0
        n_failed = 0

        for conn in ordered:
            src = pin_matrix.get(conn['source'])
            tgt = pin_matrix.get(conn['target'])
            if not src or not tgt:
                continue

            src_ref = conn['source'].split(':')[0]
            tgt_ref = conn['target'].split(':')[0]
            src_off = comp_positions.get(src_ref, (0, 0))
            tgt_off = comp_positions.get(tgt_ref, (0, 0))

            sx = round((src['x'] + src_off[0]) / GRID_SIZE) + MATRIX_OFFSET
            sy = round((src['y'] + src_off[1]) / GRID_SIZE) + MATRIX_OFFSET
            ex = round((tgt['x'] + tgt_off[0]) / GRID_SIZE) + MATRIX_OFFSET
            ey = round((tgt['y'] + tgt_off[1]) / GRID_SIZE) + MATRIX_OFFSET

            if not (0 <= sx < MATRIX_SIZE and 0 <= sy < MATRIX_SIZE and
                    0 <= ex < MATRIX_SIZE and 0 <= ey < MATRIX_SIZE):
                continue

            # Ensure start/end cells are walkable even if carve-out missed them
            self.matrix[sy][sx] = CELL_FREE
            self.matrix[ey][ex] = CELL_FREE

            # Run weighted A*
            path = _weighted_astar(self.matrix, (sx, sy), (ex, ey), MATRIX_SIZE)

            path_cells = None
            used_astar = False

            if path:
                manhattan = abs(sx - ex) + abs(sy - ey)
                if len(path) <= max(manhattan * MAX_PATH_RATIO, MIN_PATH_ABSOLUTE):
                    path_cells = path
                    used_astar = True

            # Fallback: L-shaped direct wire if A* failed or produced ghost wire
            if path_cells is None:
                # Try the pathfinding library's A* as a second attempt (different heuristic)
                try:
                    grid = Grid(matrix=self._make_binary_matrix())
                    finder = AStarFinder(diagonal_movement=DiagonalMovement.never)
                    start_node = grid.node(sx, sy)
                    end_node = grid.node(ex, ey)
                    start_node.walkable = True
                    end_node.walkable = True
                    lib_path, _ = finder.find_path(start_node, end_node, grid)
                    if lib_path:
                        manhattan = abs(sx - ex) + abs(sy - ey)
                        if len(lib_path) <= max(manhattan * MAX_PATH_RATIO, MIN_PATH_ABSOLUTE):
                            path_cells = [(n.x, n.y) for n in lib_path]
                except Exception:
                    pass

            # Final fallback: obstacle-aware wire (checks matrix for L/Z/detour variants)
            if path_cells is None:
                path_cells = _obstacle_aware_wire(sx, sy, ex, ey, self.matrix, MATRIX_SIZE)
                n_fallback += 1

            if path_cells:
                mm_path = [
                    {'x': (x - MATRIX_OFFSET) * GRID_SIZE,
                     'y': (y - MATRIX_OFFSET) * GRID_SIZE}
                    for x, y in path_cells
                ]
                traces.append({
                    'source': conn['source'],
                    'target': conn['target'],
                    'net': conn.get('net', ''),
                    'path': mm_path,
                })

                # Mark trace cells as occupied so later routes must detour
                self._mark_trace_cells(path_cells)

                if used_astar:
                    n_routed += 1
            else:
                n_failed += 1

        print(f"\n  Routing results: {n_routed} A*, {n_fallback} obstacle-aware fallback, {n_failed} failed")

        return traces


    def _make_binary_matrix(self):
        """Create a binary matrix for the pathfinding library.
        Maps CELL_FREE and CELL_PIN_CORRIDOR -> 1 (walkable),
        everything else -> 0 (blocked).
        """
        binary = []
        for row in self.matrix:
            binary.append([1 if v in (CELL_FREE, CELL_PIN_CORRIDOR) else 0 for v in row])
        return binary

    def check_and_fix_overlaps(self, traces: list, max_passes: int = 4):
        """Post-route validation: detect traces that run on top of each other
        (2+ consecutive shared cells = parallel overlap, not a crossing) and
        re-route the offenders with the other traces' cells hard-blocked.

        Returns (traces, n_fixed, n_remaining_conflicts).
        """
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

        def carve_matrix(m):
            """Apply pin carve-out to a fresh matrix copy."""
            for (gx, gy) in self._pin_carve_set:
                if 0 <= gx < MATRIX_SIZE and 0 <= gy < MATRIX_SIZE:
                    m[gy][gx] = CELL_FREE
            return m

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
                # Build a fresh matrix from the ORIGINAL obstacle matrix
                # (before any trace cells were marked)
                m = [row[:] for row in self._original_matrix]
                # Hard-block every middle cell occupied by any OTHER trace
                for j, ocs in enumerate(all_cells):
                    if j == idx:
                        continue
                    for (x, y) in ocs[1:-1]:
                        if 0 <= x < MATRIX_SIZE and 0 <= y < MATRIX_SIZE:
                            m[y][x] = CELL_BLOCKED
                carve_matrix(m)

                (sx, sy), (ex, ey) = cs[0], cs[-1]
                # Ensure pin cells walkable
                if 0 <= sx < MATRIX_SIZE and 0 <= sy < MATRIX_SIZE:
                    m[sy][sx] = CELL_FREE
                if 0 <= ex < MATRIX_SIZE and 0 <= ey < MATRIX_SIZE:
                    m[ey][ex] = CELL_FREE

                path = _weighted_astar(m, (sx, sy), (ex, ey), MATRIX_SIZE)
                if not path:
                    # Try library A* as backup
                    try:
                        binary = [[1 if v in (CELL_FREE, CELL_PIN_CORRIDOR) else 0 for v in row] for row in m]
                        grid = Grid(matrix=binary)
                        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)
                        start = grid.node(sx, sy)
                        end = grid.node(ex, ey)
                        start.walkable = True
                        end.walkable = True
                        lib_path, _ = finder.find_path(start, end, grid)
                        if lib_path:
                            path = [(n.x, n.y) for n in lib_path]
                    except Exception:
                        pass

                if not path:
                    # Obstacle-aware fallback
                    path = _obstacle_aware_wire(sx, sy, ex, ey, m, MATRIX_SIZE)

                if path:
                    manhattan = abs(sx - ex) + abs(sy - ey)
                    if len(path) <= max(manhattan * MAX_PATH_RATIO, MIN_PATH_ABSOLUTE) or len(path) <= manhattan + 4:
                        traces[idx]['path'] = [
                            {'x': (x - MATRIX_OFFSET) * GRID_SIZE,
                             'y': (y - MATRIX_OFFSET) * GRID_SIZE}
                            for x, y in path
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
            {'ref_des': c['ref_des'], 'x': c['x'], 'y': c['y']}
            for c in self.components
        ]
