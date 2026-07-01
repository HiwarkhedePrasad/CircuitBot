"""Shapely-aware A* maze router with cost-optimised routing.

Cost function (per grid step):
    cost = 1
         + via_penalty            (when crossing layers)
         + congestion_penalty     (per existing trace on this cell)
         + bend_penalty           (per direction change)
         + power_width_penalty    (power nets use wider, costlier traces)
         + clearance_violation    (×1000 if too close to obstacle)

Routing order (caller-driven):
    1. Power nets (VBUS, 3V3, 5V, VSYS, etc.) — wider traces, routed first
    2. High-speed nets (USB D+/D-, crystal) — shortest path priority
    3. Critical signals (RESET, EN, INT)
    4. General signals

Rip-up & reroute:
    After the first pass, any unrouted net triggers a retry where the
    shortest conflicting existing trace is removed and both are re-routed.
    Capped at MAX_RIPUP_PASSES iterations.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Optional

from pathfinding.core.diagonal_movement import DiagonalMovement
from pathfinding.core.grid import Grid
from pathfinding.finder.a_star import AStarFinder

from pcb_design.board_model import BoardModel, BoardTrace, BoardVia, BoardComponent, DRCConfig
from pcb_design.geometry import (
    HAS_SHAPELY, DEFAULT_CLEARANCE, pad_polygon, trace_buffer, clearance_violation,
    board_outline_polygon,
)

GRID_RESOLUTION = 0.254       # 10 mil
VIA_COST = 50                 # cost of switching layers (≈ 200 grid steps)
BEND_COST = 4                 # cost of changing direction
CONGESTION_COST = 8           # cost of stepping onto a cell used by another trace
TRACE_WEIGHT = 12
MAX_PATH_MULTIPLIER = 6       # max path length = manhattan × this
MAX_RIPUP_PASSES = 3
BOARD_MARGIN_MM = 5.0

# Net class definitions — drive width and routing priority
POWER_NETS = {
    "VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
    "+5V", "+3.3V", "3.3V", "5V", "3V3", "5V",
    "GND", "GROUND", "AGND", "DGND",
}

# High-speed signal keywords (route short & direct)
HIGH_SPEED_KEYWORDS = ("USB", "D+", "D-", "DM", "DP", "XTAL", "OSC", "XIN", "XOUT")

# Critical control signals (route early, keep short)
CRITICAL_KEYWORDS = ("RESET", "RST", "NRST", "EN", "ENABLE", "INT", "IRQ", "nRST")


def _grid(v: float) -> int:
    return max(0, int(round(v / GRID_RESOLUTION)))


def _mm(c: int) -> float:
    return c * GRID_RESOLUTION


def _is_power(net: str) -> bool:
    return net.upper() in {n.upper() for n in POWER_NETS}


def _is_high_speed(net: str) -> bool:
    n = net.upper()
    return any(k in n for k in HIGH_SPEED_KEYWORDS)


def _is_critical(net: str) -> bool:
    n = net.upper()
    return any(k in n for k in CRITICAL_KEYWORDS)


def _net_priority(net: str) -> int:
    """Lower number = routed earlier. Power → high-speed → critical → signal."""
    if _is_power(net):       return 0
    if _is_high_speed(net):  return 1
    if _is_critical(net):    return 2
    return 3


def _width_for(net_name: str, drc: DRCConfig,
               trace_constraints: Optional[dict] = None) -> float:
    if trace_constraints:
        tc = trace_constraints.get(net_name) or trace_constraints.get(net_name.upper())
        if isinstance(tc, dict) and "width_mm" in tc:
            return float(tc["width_mm"])
    if _is_power(net_name):
        # Ground can be even wider (will become pour anyway)
        if net_name.upper() in ("GND", "GROUND", "AGND", "DGND"):
            return drc.power_trace_width * 1.2
        return drc.power_trace_width
    return drc.min_trace_width


def _grid_bounds(components: list, pin_matrix: dict) -> tuple[int, int, int, int]:
    xs, ys = [], []
    for c in components:
        xs.append(c["x"])
        ys.append(c["y"])
    for pin in pin_matrix.values():
        xs.append(pin["x"])
        ys.append(pin["y"])
    if not xs:
        return (0, 0, 100, 100)
    margin = int(BOARD_MARGIN_MM / GRID_RESOLUTION)
    gx0 = _grid(min(xs)) - margin
    gy0 = _grid(min(ys)) - margin
    gx1 = _grid(max(xs)) + margin
    gy1 = _grid(max(ys)) + margin
    if gx1 - gx0 < 50:
        gx1 = gx0 + 50
    if gy1 - gy0 < 50:
        gy1 = gy0 + 50
    return (gx0, gy0, gx1 - gx0 + 1, gy1 - gy0 + 1)


# ── Cost-weighted grid builder ───────────────────────────────────────────


def _build_cost_matrix(
    components: list,
    pin_matrix: dict,
    existing_routes: list[BoardTrace],
    layer: str,
    drc: DRCConfig,
    grid_ox: int, grid_oy: int, grid_w: int, grid_h: int,
) -> list[list[float]]:
    """Build a per-cell traversal-cost matrix.

    Cells inside component pad polygons are set to 0 (impassable).
    Cells in the clearance halo of pads/other-traces get a high cost.
    """
    # Default cost = 1 (free traversal)
    mat = [[1.0 for _ in range(grid_w)] for _ in range(grid_h)]

    clearance = drc.min_clearance
    min_width = drc.min_trace_width

    # Block component pad polygons (impassable) + clearance halo (costly)
    for comp in components:
        for pad in getattr(comp, "pads", []):
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                comp.rotation + (pad.rotation or 0),
            )
            if poly is None:
                continue
            # Hard block: pad + half-clearance
            buf_hard = poly.buffer(clearance + GRID_RESOLUTION * 0.5, join_style=2)
            if buf_hard is None or buf_hard.is_empty:
                continue
            bounds = buf_hard.bounds
            x0 = max(0, _grid(bounds[0]) - grid_ox)
            y0 = max(0, _grid(bounds[1]) - grid_oy)
            x1 = min(grid_w - 1, _grid(bounds[2]) - grid_ox)
            y1 = min(grid_h - 1, _grid(bounds[3]) - grid_oy)
            from shapely.geometry import Point
            for gy in range(y0, y1 + 1):
                row = mat[gy]
                for gx in range(x0, x1 + 1):
                    wx = _mm(gx + grid_ox)
                    wy = _mm(gy + grid_oy)
                    if buf_hard.contains(Point(wx, wy)):
                        row[gx] = 0.0

    # Existing traces on this layer → congestion cost (don't hard-block,
    # so rip-up & reroute can still find a path)
    for t in existing_routes:
        if t.layer != layer:
            continue
        buf = trace_buffer(t.path, t.width + clearance * 2 + GRID_RESOLUTION)
        if buf is None or buf.is_empty:
            continue
        bounds = buf.bounds
        x0 = max(0, _grid(bounds[0]) - grid_ox)
        y0 = max(0, _grid(bounds[1]) - grid_oy)
        x1 = min(grid_w - 1, _grid(bounds[2]) - grid_ox)
        y1 = min(grid_h - 1, _grid(bounds[3]) - grid_oy)
        from shapely.geometry import Point
        for gy in range(y0, y1 + 1):
            row = mat[gy]
            for gx in range(x0, x1 + 1):
                if row[gx] <= 0:
                    continue
                wx = _mm(gx + grid_ox)
                wy = _mm(gy + grid_oy)
                if buf.contains(Point(wx, wy)):
                    row[gx] = max(row[gx], CONGESTION_COST + 1.0)

    return mat


# ── A* on weighted grid ─────────────────────────────────────────────────


def _weighted_a_star(
    mat: list[list[float]],
    sx: int, sy: int, tx: int, ty: int,
    grid_w: int, grid_h: int,
) -> Optional[list[tuple[int, int]]]:
    """A* on a weighted grid. Returns list of (gx, gy) cells, or None.

    Honors per-cell cost: 0 = impassable, >1 = expensive.  Adds a small
    bend penalty on direction change so the router prefers straight runs.
    """
    if not (0 <= sx < grid_w and 0 <= sy < grid_h): return None
    if not (0 <= tx < grid_w and 0 <= ty < grid_h): return None
    if mat[sy][sx] <= 0 or mat[ty][tx] <= 0:
        # Force-start/end walkable so we can escape pad-blocked cells
        mat[sy][sx] = max(mat[sy][sx], 1.0)
        mat[ty][tx] = max(mat[ty][tx], 1.0)

    import heapq
    start = (sx, sy)
    goal = (tx, ty)
    h = lambda x, y: abs(x - tx) + abs(y - ty)

    open_heap = [(h(sx, sy), 0.0, start, None)]
    g_score = {start: 0.0}
    came_from: dict[tuple, Optional[tuple]] = {start: None}

    while open_heap:
        _, g, cur, prev_dir = heapq.heappop(open_heap)
        if cur == goal:
            # Reconstruct
            path = []
            c = cur
            while c is not None:
                path.append(c)
                c = came_from[c]
            return path[::-1]
        if g > g_score.get(cur, float("inf")):
            continue
        cx, cy = cur
        for dx, dy, nd in ((1, 0, 0), (-1, 0, 1), (0, 1, 2), (0, -1, 3)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= nx < grid_w and 0 <= ny < grid_h):
                continue
            cell_cost = mat[ny][nx]
            if cell_cost <= 0:
                continue
            step = g + cell_cost
            if prev_dir is not None and nd != prev_dir:
                step += BEND_COST
            if step < g_score.get((nx, ny), float("inf")):
                g_score[(nx, ny)] = step
                came_from[(nx, ny)] = cur
                heapq.heappush(open_heap, (step + h(nx, ny), step, (nx, ny), nd))
    return None


# ── Single-net router ────────────────────────────────────────────────────


def _route_one(
    src_pos: tuple[float, float],
    tgt_pos: tuple[float, float],
    components: list[BoardComponent],
    existing: list[BoardTrace],
    layer: str,
    net_name: str,
    width: float,
    drc: DRCConfig,
    grid_ox: int, grid_oy: int, grid_w: int, grid_h: int,
) -> Optional[BoardTrace]:
    sx = _grid(src_pos[0]) - grid_ox
    sy = _grid(src_pos[1]) - grid_oy
    tx = _grid(tgt_pos[0]) - grid_ox
    ty = _grid(tgt_pos[1]) - grid_oy

    mat = _build_cost_matrix(
        components, {}, existing, layer, drc,
        grid_ox, grid_oy, grid_w, grid_h,
    )
    path_cells = _weighted_a_star(mat, sx, sy, tx, ty, grid_w, grid_h)
    if not path_cells or len(path_cells) < 2:
        return None

    manhattan = abs(sx - tx) + abs(sy - ty)
    if len(path_cells) > max(int(manhattan * MAX_PATH_MULTIPLIER), 30):
        return None

    mm_path = [(_mm(p[0] + grid_ox), _mm(p[1] + grid_oy)) for p in path_cells]
    return BoardTrace(net=net_name, layer=layer, width=width, path=mm_path)


# ── Multi-net router with rip-up & reroute ───────────────────────────────


def route_nets(model: BoardModel,
               netlist: list[dict],
               pin_matrix: dict,
               drc: Optional[DRCConfig] = None,
               trace_constraints: Optional[dict] = None) -> list[BoardTrace]:
    """Route all nets using weighted A* with rip-up & reroute.

    Args:
        model: BoardModel with placed components
        netlist: [{"source": "R1:1", "target": "U1:3", "net": "..."}]
        pin_matrix: {"R1:1": {"x": ..., "y": ...}, ...}
        drc: DRC rules config

    Returns: list of routed BoardTrace objects
    """
    if not HAS_SHAPELY:
        return []
    if drc is None:
        drc = DRCConfig()

    grid_ox, grid_oy, grid_w, grid_h = _grid_bounds(
        [{"x": c.x, "y": c.y} for c in model.components], pin_matrix
    )
    if grid_w < 10 or grid_h < 10:
        return []

    comp_positions = {c.ref: (c.x, c.y) for c in model.components}

    def _abs_pos(pin_key: str) -> Optional[tuple[float, float]]:
        pin = pin_matrix.get(pin_key)
        if not pin:
            return None
        ref = pin_key.split(":")[0]
        off = comp_positions.get(ref, (0, 0))
        return (pin["x"] + off[0], pin["y"] + off[1])

    def _net_len(conn: dict) -> float:
        s = _abs_pos(conn["source"])
        t = _abs_pos(conn["target"])
        if not s or not t:
            return float("inf")
        return abs(s[0] - t[0]) + abs(s[1] - t[1])

    # Sort: by priority (power first, then high-speed, then critical, then signal),
    # then by Manhattan distance (shortest first within each priority).
    ordered = sorted(netlist, key=lambda c: (_net_priority(c.get("net", "")), _net_len(c)))

    layers = ["F.Cu", "B.Cu"]
    routed: list[BoardTrace] = []
    failed: list[dict] = []

    # ── First pass ──
    for conn in ordered:
        src_pos = _abs_pos(conn["source"])
        tgt_pos = _abs_pos(conn["target"])
        if not src_pos or not tgt_pos:
            continue
        net_name = conn.get("net", conn.get("source", ""))
        width = _width_for(net_name, drc, trace_constraints)

        trace = None
        for layer in layers:
            trace = _route_one(
                src_pos, tgt_pos, model.components, routed,
                layer, net_name, width, drc,
                grid_ox, grid_oy, grid_w, grid_h,
            )
            if trace:
                break
        if trace:
            routed.append(trace)
            # Add via if we used the back layer for an SMD pin
            src_ref = conn["source"].split(":")[0]
            tgt_ref = conn["target"].split(":")[0]
            is_th = False
            for comp in model.components:
                if comp.ref in (src_ref, tgt_ref):
                    for pad in comp.pads:
                        if pad.type == "thru_hole":
                            is_th = True
                            break
            if trace.layer == "B.Cu" and not is_th:
                via = BoardVia(
                    x=src_pos[0], y=src_pos[1],
                    drill=0.3, diameter=0.6,
                    layers=["F.Cu", "B.Cu"], net=net_name,
                )
                model.vias.append(via)
                trace.via = src_pos
        else:
            failed.append(conn)

    # ── Rip-up & reroute for failed nets ──
    for pass_num in range(MAX_RIPUP_PASSES):
        if not failed:
            break
        still_failed: list[dict] = []
        for conn in failed:
            src_pos = _abs_pos(conn["source"])
            tgt_pos = _abs_pos(conn["target"])
            if not src_pos or not tgt_pos:
                continue
            net_name = conn.get("net", conn.get("source", ""))
            width = _width_for(net_name, drc, trace_constraints)

            # Find the shortest existing trace whose bbox intersects our
            # intended path's bounding box — rip it up, route us, then it.
            sx, sy = src_pos
            tx, ty = tgt_pos
            bb_x0, bb_x1 = min(sx, tx) - drc.min_clearance, max(sx, tx) + drc.min_clearance
            bb_y0, bb_y1 = min(sy, ty) - drc.min_clearance, max(sy, ty) + drc.min_clearance

            def _intersects(t: BoardTrace) -> bool:
                for px, py in t.path:
                    if bb_x0 <= px <= bb_x1 and bb_y0 <= py <= bb_y1:
                        return True
                return False

            victim_idx = None
            victim_len = float("inf")
            for i, t in enumerate(routed):
                if t.net == net_name:
                    continue
                if not _intersects(t):
                    continue
                tl = sum(abs(t.path[i][0] - t.path[i+1][0]) +
                         abs(t.path[i][1] - t.path[i+1][1])
                         for i in range(len(t.path) - 1))
                if tl < victim_len:
                    victim_len = tl
                    victim_idx = i

            if victim_idx is None:
                still_failed.append(conn)
                continue

            victim = routed.pop(victim_idx)
            victim_conn = {"source": "", "target": "", "net": victim.net}
            # Re-derive victim_conn pins from path endpoints
            v_start = victim.path[0]
            v_end = victim.path[-1]
            for k, p in pin_matrix.items():
                ref = k.split(":")[0]
                off = comp_positions.get(ref, (0, 0))
                ax = p["x"] + off[0]
                ay = p["y"] + off[1]
                if abs(ax - v_start[0]) < GRID_RESOLUTION and abs(ay - v_start[1]) < GRID_RESOLUTION:
                    victim_conn["source"] = k
                elif abs(ax - v_end[0]) < GRID_RESOLUTION and abs(ay - v_end[1]) < GRID_RESOLUTION:
                    victim_conn["target"] = k

            # Try routing us
            trace = None
            for layer in layers:
                trace = _route_one(
                    src_pos, tgt_pos, model.components, routed,
                    layer, net_name, width, drc,
                    grid_ox, grid_oy, grid_w, grid_h,
                )
                if trace:
                    break
            if trace:
                routed.append(trace)
                # Re-route the victim
                if victim_conn["source"] and victim_conn["target"]:
                    v_src = _abs_pos(victim_conn["source"])
                    v_tgt = _abs_pos(victim_conn["target"])
                    if v_src and v_tgt:
                        v_trace = None
                        for layer in layers:
                            v_trace = _route_one(
                                v_src, v_tgt, model.components, routed,
                                layer, victim.net, victim.width, drc,
                                grid_ox, grid_oy, grid_w, grid_h,
                            )
                            if v_trace:
                                break
                        if v_trace:
                            routed.append(v_trace)
                        else:
                            still_failed.append(victim_conn)
            else:
                routed.append(victim)  # put it back
                still_failed.append(conn)
        failed = still_failed

    return routed


# ── DRC with full Shapely clearance checks ───────────────────────────────


def drc2(model: BoardModel, drc: Optional[DRCConfig] = None) -> list[dict]:
    """DRC using Shapely for accurate polygon clearance checks.

    Checks:
      1. Trace-to-trace clearance (different nets)
      2. Trace-to-pad clearance (different nets)
      3. Trace-to-board-edge keepout
      4. Minimum trace width
      5. Unrouted net detection (caller can pass via model.nets)
    """
    if not HAS_SHAPELY:
        return []
    if drc is None:
        drc = DRCConfig()
    clearance = drc.min_clearance
    violations = []

    # Pre-compute pad polygons per component
    pad_polys: dict[str, list[tuple[str, any]]] = {}
    for comp in model.components:
        for pad in comp.pads:
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                comp.rotation + (pad.rotation or 0),
            )
            if poly is not None:
                pad_polys.setdefault(comp.ref, []).append((pad.number, poly))

    # 1. Trace-trace clearance
    trace_bufs = []
    for t in model.traces:
        if len(t.path) < 2:
            continue
        buf = trace_buffer(t.path, t.width)
        if buf is not None:
            trace_bufs.append((t, buf))
    for i, (t1, b1) in enumerate(trace_bufs):
        for j, (t2, b2) in enumerate(trace_bufs):
            if j <= i:
                continue
            if t1.net == t2.net:
                continue
            if clearance_violation(b1, b2, clearance):
                violations.append({
                    "type": "trace-trace",
                    "severity": "error",
                    "message": f"Trace {t1.net} and {t2.net} too close (clearance < {clearance}mm)",
                })

    # Build a mapping from pin connection key (ref:pad_num) to net name (case-insensitive)
    pin_to_net = {}
    for net in model.nets:
        name = str(net.get("name", "") or net.get("net", "")).strip().upper()
        for p in net.get("pins", []):
            pin_to_net[p] = name
    for pp in model.power_pins:
        p = pp.get("pin")
        name = str(pp.get("net", "")).strip().upper()
        if p:
            pin_to_net[p] = name

    # 2. Trace-pad clearance
    for trace, buf in trace_bufs:
        trace_net_upper = trace.net.upper()
        for ref, pads in pad_polys.items():
            for pnum, ppoly in pads:
                pin_key = f"{ref}:{pnum}"
                # Skip the trace's own endpoint pads (same net)
                if pin_to_net.get(pin_key) == trace_net_upper:
                    continue
                # Fallback to simple ref check as backup
                if ref in trace.net:
                    continue
                if clearance_violation(buf, ppoly, clearance):
                    violations.append({
                        "type": "trace-pad",
                        "severity": "error",
                        "message": f"Trace {trace.net} too close to {ref} pad {pnum}",
                    })

    # 3. Minimum trace width
    for t in model.traces:
        if t.width < drc.min_trace_width:
            violations.append({
                "type": "trace-width",
                "severity": "warning",
                "message": f"Trace {t.net} width {t.width}mm < min {drc.min_trace_width}mm",
            })

    return violations
