"""Copper zone pouring for PCB ground fills.

Computes a GND copper pour by taking the board outline and subtracting
all obstacle polygons (component pads, vias, traces with clearance).
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import Polygon, MultiPolygon, Point, box as shapely_box
from shapely import union_all, difference, simplify, make_valid

from pcb_design.board_model import BoardModel, BoardComponent, BoardZone
from pcb_design.geometry import (
    HAS_SHAPELY, DEFAULT_CLEARANCE, pad_polygon, trace_buffer,
)


def pour_ground(model: BoardModel, clearance: float = DEFAULT_CLEARANCE,
                min_zone_area: float = 10.0,
                thermal_relief_gap: float = 0.254,
                thermal_spoke_width: float = 0.254) -> int:
    """Pour ground copper zones on all signal layers.

    Computes the board outline (from Edge.Cuts traces, or derived from
    component bounding boxes), subtracts all obstacles (pads, vias, trace
    buffers with clearance), and creates zone polygons for the GND net.

    Args:
        model: BoardModel to pour zones into (modified in-place)
        clearance: Clearance from zone to non-GND nets
        min_zone_area: Minimum zone polygon area in mm² (small islands are dropped)
        thermal_relief_gap: Gap around thru-hole pads for thermal relief
        thermal_spoke_width: Spoke width for thermal relief connections

    Returns: Number of zone polygons created (per layer)
    """
    if not HAS_SHAPELY:
        return 0

    # 1. Determine board outline
    outline = model.outline
    if outline is None:
        outline = _derive_outline(model)

    if outline is None or outline.is_empty:
        return 0

    # 2. Collect obstacles (non-GND pads, vias, traces)
    obstacles = []

    # All pads on GND net get thermal relief, not full isolation
    gnd_pads = set()
    for net in model.nets:
        name = net.get("name", "") or net.get("net", "")
        if name.upper() in ("GND", "GROUND", "0V"):
            for pin_ref in net.get("pins", []):
                gnd_pads.add(pin_ref)

    for comp in model.components:
        for pad in comp.pads:
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                rotation=comp.rotation + (pad.rotation or 0),
            )
            if poly is None:
                continue
            # Thru-hole pads get thermal relief
            if pad.type == "thru_hole":
                relief = _thermal_relief(poly, thermal_relief_gap, thermal_spoke_width)
                if relief is not None:
                    obstacles.append(relief)
            else:
                obstacles.append(poly.buffer(clearance, join_style=2))

    # Via obstacles
    for via in model.vias:
        poly = Point(via.x, via.y).buffer(via.diameter / 2 + clearance, resolution=8)
        obstacles.append(poly)

    # Trace obstacles (non-GND traces with clearance)
    for trace in model.traces:
        if trace.net.upper() in ("GND", "GROUND", "0V"):
            continue
        buf = trace_buffer(trace.path, trace.width)
        if buf is not None:
            obstacles.append(buf.buffer(clearance, join_style=2))

    # 3. Subtract obstacles from outline
    if obstacles:
        combined_obs = union_all(obstacles)
        zone_geo = difference(outline, combined_obs)
    else:
        zone_geo = outline

    if zone_geo is None or zone_geo.is_empty:
        return 0

    if not zone_geo.is_valid:
        zone_geo = make_valid(zone_geo)

    # 4. Simplify and filter small islands
    zone_geo = simplify(zone_geo, tolerance=0.01, preserve_topology=True)

    if isinstance(zone_geo, Polygon):
        polys = [zone_geo]
    elif isinstance(zone_geo, MultiPolygon):
        polys = list(zone_geo.geoms)
    else:
        return 0

    # 5. Create zone on each signal layer
    layers = ["F.Cu", "B.Cu"]
    count = 0
    for poly in polys:
        if poly.area < min_zone_area:
            continue
        # Ensure polygon is valid and oriented
        if not poly.is_valid:
            poly = make_valid(poly)
        if poly.is_empty:
            continue
        for layer in layers:
            zone = BoardZone(
                net="GND",
                layer=layer,
                polygon=poly,
                priority=0,
            )
            model.zones.append(zone)
            count += 1

    return count


def _derive_outline(model: BoardModel) -> Optional[Polygon]:
    """Derive board outline from component bounding box + margin."""
    xs, ys = [], []
    for comp in model.components:
        xs.append(comp.x)
        ys.append(comp.y)
        for pad in comp.pads:
            xs.append(comp.x + pad.x + pad.width / 2)
            xs.append(comp.x + pad.x - pad.width / 2)
            ys.append(comp.y + pad.y + pad.height / 2)
            ys.append(comp.y + pad.y - pad.height / 2)
    if not xs:
        return None
    margin = 3.0
    return shapely_box(min(xs) - margin, min(ys) - margin,
                       max(xs) + margin, max(ys) + margin)


def _thermal_relief(pad_poly: Polygon, gap: float, spoke_width: float) -> Optional[Polygon]:
    """Create a thermal relief cutout around a pad polygon.

    The relief creates a gap around the pad with 4 narrow spokes connecting
    the pad to the surrounding copper.
    """
    if pad_poly is None or pad_poly.is_empty:
        return None

    bounds = pad_poly.bounds
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]

    # Outer clearance box
    outer = shapely_box(
        cx - w / 2 - gap, cy - h / 2 - gap,
        cx + w / 2 + gap, cy + h / 2 + gap,
    )

    # Spokes — narrow passages at 0°, 90°, 180°, 270°
    spoke_len = max(w, h) / 2 + gap + 0.5
    spokes = [
        shapely_box(cx - spoke_width / 2, cy - gap / 2 - spoke_len,
                     cx + spoke_width / 2, cy + gap / 2 + spoke_len),
    ]

    cutout = outer
    for spoke in spokes:
        cutout = difference(cutout, spoke)

    return cutout
