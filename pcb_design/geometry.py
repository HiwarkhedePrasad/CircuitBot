"""Geometry utilities for PCB design using Shapely.

Provides high-level wrappers for common PCB geometry operations:
pad polygons, clearance checks, keepout regions, trace buffering.
"""

from __future__ import annotations

from typing import Optional

try:
    from shapely.geometry import Point, Polygon, LineString, MultiPolygon, box as shapely_box
    from shapely import affinity, union_all, difference
    import shapely
    HAS_SHAPELY = True
except ImportError:
    HAS_SHAPELY = False
    Point = Polygon = LineString = MultiPolygon = None


DEFAULT_CLEARANCE = 0.254


def pad_polygon(x: float, y: float, width: float, height: float,
                shape: str = "rect", rotation: float = 0.0) -> Optional[Polygon]:
    if not HAS_SHAPELY:
        return None
    hw, hh = width / 2, height / 2
    if shape == "circle":
        poly = Point(0, 0).buffer(max(hw, hh), resolution=16)
    elif shape == "oval":
        r = min(hw, hh)
        if hw > hh:
            poly = shapely_box(-hw, -hh, hw, hh).buffer(r, resolution=16, join_style=2)
        else:
            poly = shapely_box(-hw, -hh, hw, hh).buffer(r, resolution=16, join_style=2)
    else:
        poly = shapely_box(-hw, -hh, hw, hh)
    poly = affinity.translate(poly, x, y)
    if rotation:
        poly = affinity.rotate(poly, rotation, origin=(x, y), use_radians=False)
    return poly


def trace_buffer(path: list[tuple[float, float]], width: float) -> Optional[Polygon]:
    if not HAS_SHAPELY or len(path) < 2:
        return None
    ls = LineString(path)
    return ls.buffer(width / 2, cap_style=2, join_style=2)


def clearance_violation(geo_a, geo_b, min_dist: float = DEFAULT_CLEARANCE) -> bool:
    if not HAS_SHAPELY:
        return False
    return geo_a.distance(geo_b) < min_dist


def keepout_region(component_polys: list[Polygon], board_outline: Polygon,
                   margin: float = DEFAULT_CLEARANCE) -> Optional[Polygon]:
    if not HAS_SHAPELY:
        return None
    if not component_polys:
        return board_outline
    combined = union_all([p.buffer(margin, join_style=2) for p in component_polys])
    return difference(board_outline, combined)


def component_outline(pads: list[dict], margin: float = 1.0) -> Optional[Polygon]:
    if not HAS_SHAPELY or not pads:
        return None
    xs = [p.get("x", 0) for p in pads]
    ys = [p.get("y", 0) for p in pads]
    return shapely_box(min(xs) - margin, min(ys) - margin,
                       max(xs) + margin, max(ys) + margin)


def board_bounds(components: list[dict]) -> tuple[float, float, float, float]:
    xs = []
    ys = []
    for c in components:
        x, y = c.get("x", 0), c.get("y", 0)
        pads = c.get("pads", [])
        if pads:
            px = [x + p.get("x", 0) for p in pads]
            py = [y + p.get("y", 0) for p in pads]
            xs.extend(px)
            ys.extend(py)
        else:
            xs.append(x)
            ys.append(y)
    if not xs:
        return (-50, -50, 50, 50)
    margin = 5.0
    return (min(xs) - margin, min(ys) - margin,
            max(xs) + margin, max(ys) + margin)


def board_outline_polygon(components: list[dict],
                          margin: float = 5.0) -> Optional[Polygon]:
    if not HAS_SHAPELY:
        return None
    xmin, ymin, xmax, ymax = board_bounds(components)
    return shapely_box(xmin - margin, ymin - margin,
                       xmax + margin, ymax + margin)


def board_outline_segments(components: list[dict],
                           margin: float = 5.0,
                           corner_radius: float = 2.0) -> list[dict]:
    """Create a rounded-rectangle board outline as KiCad Edge.Cuts segments.

    Returns a list of gr_line and gr_arc dicts in the same format as
    ``_parse_outline_segment()`` in ``pcb_design/pcb_import.py``.
    """
    import math

    xmin, ymin, xmax, ymax = board_bounds(components)
    # Apply margin
    xmin -= margin
    ymin -= margin
    xmax += margin
    ymax += margin

    r = min(corner_radius, (xmax - xmin) / 4, (ymax - ymin) / 4)
    if r < 0.1:
        # No room for rounded corners — emit 4 straight lines
        return [
            {"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
             "start": {"x": xmin, "y": ymin}, "end": {"x": xmax, "y": ymin}},
            {"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
             "start": {"x": xmax, "y": ymin}, "end": {"x": xmax, "y": ymax}},
            {"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
             "start": {"x": xmax, "y": ymax}, "end": {"x": xmin, "y": ymax}},
            {"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
             "start": {"x": xmin, "y": ymax}, "end": {"x": xmin, "y": ymin}},
        ]

    segments: list[dict] = []

    def _pt(cx, cy, angle_deg):
        rad = math.radians(angle_deg)
        return {"x": round(cx + r * math.cos(rad), 4),
                "y": round(cy + r * math.sin(rad), 4)}

    # Bottom-left corner arc center
    bl_cx, bl_cy = xmin + r, ymin + r
    # Bottom-right corner arc center
    br_cx, br_cy = xmax - r, ymin + r
    # Top-right corner arc center
    tr_cx, tr_cy = xmax - r, ymax - r
    # Top-left corner arc center
    tl_cx, tl_cy = xmin + r, ymax - r

    # Bottom edge: line from BL corner end to BR corner start
    segments.append({"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(bl_cx, bl_cy, 270),
                     "end": _pt(br_cx, br_cy, 270)})
    # Bottom-right corner arc
    segments.append({"kind": "gr_arc", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(br_cx, br_cy, 270),
                     "mid": _pt(br_cx, br_cy, 315),
                     "end": _pt(br_cx, br_cy, 0)})
    # Right edge: line from BR corner end to TR corner start
    segments.append({"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(br_cx, br_cy, 0),
                     "end": _pt(tr_cx, tr_cy, 0)})
    # Top-right corner arc
    segments.append({"kind": "gr_arc", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(tr_cx, tr_cy, 0),
                     "mid": _pt(tr_cx, tr_cy, 45),
                     "end": _pt(tr_cx, tr_cy, 90)})
    # Top edge: line from TR corner end to TL corner start
    segments.append({"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(tr_cx, tr_cy, 90),
                     "end": _pt(tl_cx, tl_cy, 90)})
    # Top-left corner arc
    segments.append({"kind": "gr_arc", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(tl_cx, tl_cy, 90),
                     "mid": _pt(tl_cx, tl_cy, 135),
                     "end": _pt(tl_cx, tl_cy, 180)})
    # Left edge: line from TL corner end to BL corner start
    segments.append({"kind": "gr_line", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(tl_cx, tl_cy, 180),
                     "end": _pt(bl_cx, bl_cy, 180)})
    # Bottom-left corner arc
    segments.append({"kind": "gr_arc", "layer": "Edge.Cuts", "width": 0.1,
                     "start": _pt(bl_cx, bl_cy, 180),
                     "mid": _pt(bl_cx, bl_cy, 225),
                     "end": _pt(bl_cx, bl_cy, 270)})

    return segments
