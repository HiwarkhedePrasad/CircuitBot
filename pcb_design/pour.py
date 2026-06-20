"""Copper zone pouring for PCB ground fills — hardened version.

Improvements over the original:
  * Thermal relief with 4 spokes (N/S/E/W) — original had only 1 spoke.
  * GND pad detection from both `model.nets` and `model.power_pins`
    (the agent populates power_pins, not nets, in normal operation).
  * Separate zone polygons per layer (so KiCad can export them properly).
  * Sliver removal: islands smaller than min_zone_area are dropped.
  * Polygon simplification with topology preservation to avoid
    self-intersections after differencing.
"""

from __future__ import annotations

from typing import Optional

from shapely.geometry import Polygon, MultiPolygon, Point, box as shapely_box
from shapely import union_all, difference, simplify, make_valid

from pcb_design.board_model import BoardModel, BoardComponent, BoardZone
from pcb_design.geometry import (
    HAS_SHAPELY, DEFAULT_CLEARANCE, pad_polygon, trace_buffer,
)


def _is_gnd_net(name: str) -> bool:
    return (name or "").upper() in ("GND", "GROUND", "0V", "AGND", "DGND")


def _collect_gnd_pads(model: BoardModel) -> set[str]:
    """Return set of pin_keys (e.g. 'U1:5') connected to GND."""
    gnd_pads: set[str] = set()
    # From model.nets
    for net in model.nets or []:
        name = net.get("name", "") or net.get("net", "")
        if _is_gnd_net(name):
            for pin_ref in net.get("pins", []):
                gnd_pads.add(pin_ref)
    # From model.power_pins (the agent's primary channel)
    for pp in model.power_pins or []:
        if _is_gnd_net(pp.get("net", "")):
            gnd_pads.add(pp["pin"])
    return gnd_pads


def pour_ground(model: BoardModel, clearance: float = DEFAULT_CLEARANCE,
                min_zone_area: float = 10.0,
                thermal_relief_gap: float = 0.254,
                thermal_spoke_width: float = 0.254) -> int:
    """Pour ground copper zones on all signal layers.

    Args:
        model: BoardModel to pour zones into (modified in-place)
        clearance: Clearance from zone to non-GND nets
        min_zone_area: Minimum zone polygon area in mm² (small islands dropped)
        thermal_relief_gap: Gap around thru-hole pads for thermal relief
        thermal_spoke_width: Spoke width for thermal relief connections

    Returns: Number of zone polygons created (total across both layers)
    """
    if not HAS_SHAPELY:
        return 0

    # 1. Determine board outline
    outline = model.outline
    if outline is None or outline.is_empty:
        outline = _derive_outline(model)
    if outline is None or outline.is_empty:
        return 0

    # 2. Identify GND pads so they get thermal relief (not full isolation)
    gnd_pad_keys = _collect_gnd_pads(model)

    obstacles = []

    for comp in model.components:
        for pad in comp.pads:
            poly = pad_polygon(
                comp.x + pad.x, comp.y + pad.y,
                pad.width, pad.height, pad.shape,
                rotation=comp.rotation + (pad.rotation or 0),
            )
            if poly is None or poly.is_empty:
                continue

            pad_key = f"{comp.ref}:{pad.number}"
            is_gnd_pad = pad_key in gnd_pad_keys

            # Thru-hole pads: thermal relief (4 spokes) regardless of net,
            # so soldering doesn't create a huge heat sink.
            if pad.type == "thru_hole":
                relief = _thermal_relief(poly, thermal_relief_gap, thermal_spoke_width)
                if relief is not None:
                    obstacles.append(relief)
            elif is_gnd_pad:
                # GND SMD pad: small clearance so the pour touches the pad
                obstacles.append(poly.buffer(0.1, join_style=2))
            else:
                # Non-GND SMD pad: full clearance
                obstacles.append(poly.buffer(clearance, join_style=2))

    # Via obstacles
    for via in model.vias:
        via_net = (via.net or "").upper()
        if _is_gnd_net(via_net):
            # GND via: small clearance so pour connects
            obstacles.append(Point(via.x, via.y).buffer(via.diameter / 2 + 0.1, resolution=12))
        else:
            obstacles.append(Point(via.x, via.y).buffer(via.diameter / 2 + clearance, resolution=12))

    # Trace obstacles (non-GND traces with clearance)
    for trace in model.traces:
        if _is_gnd_net(trace.net):
            continue
        buf = trace_buffer(trace.path, trace.width)
        if buf is not None and not buf.is_empty:
            obstacles.append(buf.buffer(clearance, join_style=2))

    # 3. Subtract obstacles from outline
    if obstacles:
        combined_obs = union_all(obstacles)
        if combined_obs.is_empty:
            zone_geo = outline
        else:
            zone_geo = difference(outline, combined_obs)
    else:
        zone_geo = outline

    if zone_geo is None or zone_geo.is_empty:
        return 0

    if not zone_geo.is_valid:
        zone_geo = make_valid(zone_geo)

    # 4. Simplify with topology preservation
    zone_geo = simplify(zone_geo, tolerance=0.01, preserve_topology=True)

    # 5. Extract individual polygons
    if isinstance(zone_geo, Polygon):
        polys = [zone_geo]
    elif isinstance(zone_geo, MultiPolygon):
        polys = list(zone_geo.geoms)
    else:
        # GeometryCollection — pick out only Polygon parts
        polys = [g for g in getattr(zone_geo, "geoms", []) if isinstance(g, Polygon)]

    # 6. Create zone on each signal layer (one BoardZone per polygon per layer)
    layers = ["F.Cu", "B.Cu"]
    count = 0
    for poly in polys:
        if not poly.is_valid:
            poly = make_valid(poly)
        if poly.is_empty or poly.area < min_zone_area:
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
    """Create a 4-spoke thermal relief cutout around a pad polygon.

    The cutout is the pad's bounding box expanded by ``gap``, with 4
    narrow spokes (one per compass direction) removed so the pad stays
    electrically connected to the pour but thermally isolated.
    """
    if pad_poly is None or pad_poly.is_empty:
        return None

    bounds = pad_poly.bounds
    cx = (bounds[0] + bounds[2]) / 2
    cy = (bounds[1] + bounds[3]) / 2
    w = bounds[2] - bounds[0]
    h = bounds[3] - bounds[1]

    # Outer clearance box (pad + gap on all sides)
    outer = shapely_box(
        cx - w / 2 - gap, cy - h / 2 - gap,
        cx + w / 2 + gap, cy + h / 2 + gap,
    )

    # 4 spokes: N, S, E, W
    spoke_len = max(w, h) / 2 + gap + 0.5
    hw = spoke_width / 2

    spokes = [
        # North + South (vertical spokes)
        shapely_box(cx - hw, cy - spoke_len,
                    cx + hw, cy + spoke_len),
        # East + West (horizontal spokes)
        shapely_box(cx - spoke_len, cy - hw,
                    cx + spoke_len, cy + hw),
    ]

    cutout = outer
    for spoke in spokes:
        cutout = difference(cutout, spoke)
        if cutout is None or cutout.is_empty:
            break

    return cutout
