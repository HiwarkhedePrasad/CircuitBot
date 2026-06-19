"""Shapely-aware maze router with multi-layer support and vias.

Uses the same pin_matrix + component_position convention as the
original layout_engine.py, but builds obstacle grids from Shapely
polygons for better clearance accuracy.
"""

from __future__ import annotations

import math
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
VIA_COST = 50
TRACE_WEIGHT = 12
MAX_PATH_MULTIPLIER = 4
BOARD_MARGIN_MM = 5.0


def _grid(v: float) -> int:
    return max(0, int(round(v / GRID_RESOLUTION)))


def _mm(c: int) -> float:
    return c * GRID_RESOLUTION


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


def route_nets(model: BoardModel,
               netlist: list[dict],
               pin_matrix: dict,
               drc: Optional[DRCConfig] = None) -> list[BoardTrace]:
    """Route all nets. Uses pin_matrix for pin positions (same convention as old router).

    Args:
        model: BoardModel with placed components
        netlist: [{"source": "R1:1", "target": "U1:3"}, ...]
        pin_matrix: {"R1:1": {"x": ..., "y": ..., ...}, ...}
        drc: DRC rules config (clearance, trace widths)

    Returns: list of routed BoardTrace objects
    """
    if not HAS_SHAPELY:
        return []

    if drc is None:
        drc = DRCConfig()
    clearance = drc.min_clearance
    min_width = drc.min_trace_width
    power_width = drc.power_trace_width

    grid_ox, grid_oy, grid_w, grid_h = _grid_bounds(
        [{"x": c.x, "y": c.y} for c in model.components], pin_matrix
    )
    if grid_w < 10 or grid_h < 10:
        return []

    power_nets = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT", "+5V", "+3.3V", "3.3V", "5V"}

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

    def _width_for(net_name: str) -> float:
        return power_width if net_name.upper() in power_nets else min_width

    routed: list[BoardTrace] = []
    layers = ["F.Cu", "B.Cu"]

    # Build component pad polygons for obstacle map
    comp_pad_polys = []
    for comp in model.components:
        for pad in comp.pads:
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                comp.rotation + (pad.rotation or 0),
            )
            if poly is not None:
                comp_pad_polys.append((poly, clearance))

    def _build_matrix(existing_routes: list[BoardTrace], layer: str) -> Grid:
        mat = [[1 for _ in range(grid_w)] for _ in range(grid_h)]

        def _block(cx, cy, r):
            x0 = max(0, _grid(cx - r) - grid_ox)
            y0 = max(0, _grid(cy - r) - grid_oy)
            x1 = min(grid_w - 1, _grid(cx + r) - grid_ox)
            y1 = min(grid_h - 1, _grid(cy + r) - grid_oy)
            for gy in range(y0, y1 + 1):
                row = mat[gy]
                for gx in range(x0, x1 + 1):
                    row[gx] = 0

        # Block from Shapely pad polygons
        for poly, clr in comp_pad_polys:
            if poly is None:
                continue
            buf = poly.buffer(clr, join_style=2)
            if buf is None or buf.is_empty:
                continue
            bounds = buf.bounds
            x0 = max(0, _grid(bounds[0]) - grid_ox)
            y0 = max(0, _grid(bounds[1]) - grid_oy)
            x1 = min(grid_w - 1, _grid(bounds[2]) - grid_ox)
            y1 = min(grid_h - 1, _grid(bounds[3]) - grid_oy)
            for gy in range(y0, y1 + 1):
                row = mat[gy]
                for gx in range(x0, x1 + 1):
                    wx = _mm(gx + grid_ox)
                    wy = _mm(gy + grid_oy)
                    from shapely.geometry import Point
                    if buf.contains(Point(wx, wy)):
                        row[gx] = 0

        # Block existing traces on this layer
        for t in existing_routes:
            if t.layer != layer:
                continue
            buf = trace_buffer(t.path, t.width + clearance * 2)
            if buf is None:
                continue
            bounds = buf.bounds
            x0 = max(0, _grid(bounds[0]) - grid_ox)
            y0 = max(0, _grid(bounds[1]) - grid_oy)
            x1 = min(grid_w - 1, _grid(bounds[2]) - grid_ox)
            y1 = min(grid_h - 1, _grid(bounds[3]) - grid_oy)
            for gy in range(y0, y1 + 1):
                row = mat[gy]
                for gx in range(x0, x1 + 1):
                    wx = _mm(gx + grid_ox)
                    wy = _mm(gy + grid_oy)
                    from shapely.geometry import Point
                    if buf.contains(Point(wx, wy)):
                        row[gx] = 0

        return Grid(matrix=mat)

    def _route_one(src_pos: tuple[float, float],
                   tgt_pos: tuple[float, float],
                   existing: list[BoardTrace],
                   layer: str, net_name: str, width: float) -> Optional[BoardTrace]:
        sx = _grid(src_pos[0]) - grid_ox
        sy = _grid(src_pos[1]) - grid_oy
        tx = _grid(tgt_pos[0]) - grid_ox
        ty = _grid(tgt_pos[1]) - grid_oy

        if not (0 <= sx < grid_w and 0 <= sy < grid_h):
            return None
        if not (0 <= tx < grid_w and 0 <= ty < grid_h):
            return None

        grid = _build_matrix(existing, layer)
        grid.cleanup()
        start = grid.node(sx, sy)
        end = grid.node(tx, ty)
        start.walkable = True
        end.walkable = True

        finder = AStarFinder(diagonal_movement=DiagonalMovement.never)
        path, _ = finder.find_path(start, end, grid)
        if not path or len(path) < 2:
            return None

        manhattan = abs(sx - tx) + abs(sy - ty)
        if len(path) > max(int(manhattan * MAX_PATH_MULTIPLIER), 30):
            return None

        mm_path = [(_mm(p[0] + grid_ox), _mm(p[1] + grid_oy)) for p in path]
        return BoardTrace(net=net_name, layer=layer, width=width, path=mm_path)

    # Sort nets by Manhattan distance (shortest first)
    ordered = sorted(netlist, key=_net_len)

    for conn in ordered:
        src_pos = _abs_pos(conn["source"])
        tgt_pos = _abs_pos(conn["target"])
        if not src_pos or not tgt_pos:
            continue

        net_name = conn.get("net", conn.get("source", ""))
        width = _width_for(net_name)

        trace = None
        for layer in layers:
            trace = _route_one(src_pos, tgt_pos, routed, layer, net_name, width)
            if trace:
                break

        if trace:
            routed.append(trace)
            # If trace is on B.Cu and both pins are on F.Cu, add vias
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
                # Add via at start
                trace.via = src_pos
                via = BoardVia(
                    x=src_pos[0], y=src_pos[1],
                    drill=0.3, diameter=0.6,
                    layers=["F.Cu", "B.Cu"], net=net_name,
                )
                model.vias.append(via)

    return routed


def drc2(model: BoardModel, drc: Optional[DRCConfig] = None) -> list[dict]:
    """DRC using Shapely for accurate polygon clearance checks."""
    if not HAS_SHAPELY:
        return []
    if drc is None:
        drc = DRCConfig()
    clearance = drc.min_clearance
    violations = []

    # Pre-compute pad polygons per component
    pad_polys = {}
    for comp in model.components:
        for pad in comp.pads:
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                comp.rotation + (pad.rotation, 0),
            )
            if poly is not None:
                pad_polys.setdefault(comp.ref, []).append((pad.number, poly))

    # Trace-trace clearance
    for i, t1 in enumerate(model.traces):
        buf1 = trace_buffer(t1.path, t1.width) if len(t1.path) >= 2 else None
        if buf1 is None:
            continue
        for j, t2 in enumerate(model.traces):
            if j <= i:
                continue
            if t1.net == t2.net:
                continue
            buf2 = trace_buffer(t2.path, t2.width) if len(t2.path) >= 2 else None
            if buf2 is None:
                continue
            if clearance_violation(buf1, buf2, clearance):
                violations.append({
                    "type": "trace-trace",
                    "severity": "error",
                    "message": f"Trace {t1.net} and {t2.net} too close (clearance {clearance}mm)",
                })

    # Trace-pad clearance
    for trace in model.traces:
        buf = trace_buffer(trace.path, trace.width) if len(trace.path) >= 2 else None
        if buf is None:
            continue
        for ref, pads in pad_polys.items():
            for pnum, ppoly in pads:
                if clearance_violation(buf, ppoly, clearance):
                    violations.append({
                        "type": "trace-pad",
                        "severity": "error",
                        "message": f"Trace {trace.net} too close to {ref} pad {pnum}",
                    })

    return violations
