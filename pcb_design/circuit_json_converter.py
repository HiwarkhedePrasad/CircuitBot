"""BoardModel → Circuit JSON converter.

Produces a Circuit-JSON array accepted by ``@tscircuit/runframe`` /
``@tscircuit/pcb-viewer`` so that the home-grown Canvas2D PCB viewer
can be replaced by the tscircuit rendering pipeline.
"""

from __future__ import annotations

import math
from typing import Any

from .board_model import BoardComponent, BoardModel, BoardTrace, BoardVia

SELF_LAYER_MAP = {
    "F.Cu": "top",
    "B.Cu": "bottom",
    "F.SilkS": "top",
    "B.SilkS": "bottom",
    "Edge.Cuts": "top",
}


def _sanitize_id(s: str) -> str:
    return s.replace("/", "_").replace(".", "_").replace(" ", "_").replace("-", "_")


def _comp_bbox(comp: BoardComponent) -> tuple[float, float, float, float]:
    xs = [p.x for p in comp.pads] or [0]
    ys = [p.y for p in comp.pads] or [0]
    margin = 0.5
    return (
        min(xs) - margin,
        min(ys) - margin,
        max(xs) + margin,
        max(ys) + margin,
    )


def _layer_to_ts(layer: str) -> str:
    return SELF_LAYER_MAP.get(layer, "top")


def _compute_outline(model: BoardModel) -> list[dict[str, float]]:
    coords: list[tuple[float, float]] = []
    if model.outline is not None:
        try:
            exterior = model.outline.exterior
            coords = list(exterior.coords)
        except Exception:
            pass
    if not coords:
        xs: list[float] = []
        ys: list[float] = []
        for c in model.components:
            xs.append(c.x)
            ys.append(c.y)
        for t in model.traces:
            for pt in t.path:
                xs.append(pt[0])
                ys.append(pt[1])
        if xs and ys:
            margin = 5.0
            minx, maxx = min(xs) - margin, max(xs) + margin
            miny, maxy = min(ys) - margin, max(ys) + margin
            coords = [
                (minx, miny),
                (maxx, miny),
                (maxx, maxy),
                (minx, maxy),
                (minx, miny),
            ]
        else:
            coords = [(-25, -20), (25, -20), (25, 20), (-25, 20), (-25, -20)]
    return [{"x": round(c[0], 4), "y": round(c[1], 4)} for c in coords]


def board_model_to_circuit_json(model: BoardModel) -> list[dict[str, Any]]:
    elements: list[dict[str, Any]] = []

    pcb_id_map: dict[str, str] = {}
    source_comp_map: dict[str, str] = {}

    outline = _compute_outline(model)
    xs = [p["x"] for p in outline]
    ys = [p["y"] for p in outline]
    cx = round((min(xs) + max(xs)) / 2, 4)
    cy = round((min(ys) + max(ys)) / 2, 4)

    elements.append({
        "type": "pcb_board",
        "pcb_board_id": "pcb_board_1",
        "center": {"x": cx, "y": cy},
        "thickness": 1.6,
        "num_layers": 2,
        "material": "fr4",
        "outline": outline,
    })

    for comp in model.components:
        cid = _sanitize_id(comp.ref)
        source_id = f"source_component_{cid}"
        pcb_id = f"pcb_component_{cid}"
        source_comp_map[comp.ref] = source_id
        pcb_id_map[comp.ref] = pcb_id

        bbox = _comp_bbox(comp)
        w = round(bbox[2] - bbox[0], 4)
        h = round(bbox[3] - bbox[1], 4)

        elements.append({
            "type": "source_component",
            "source_component_id": source_id,
            "name": comp.ref,
            "ftype": "simple_pcb_component",
        })

        elements.append({
            "type": "pcb_component",
            "pcb_component_id": pcb_id,
            "source_component_id": source_id,
            "center": {"x": comp.x, "y": comp.y},
            "layer": _layer_to_ts(comp.layer),
            "rotation": comp.rotation,
            "width": w or 3.0,
            "height": h or 3.0,
        })

        pad_index = 0
        for pad in comp.pads:
            pad_index += 1
            sp_id = f"source_port_{cid}_{pad.number}"
            pp_id = f"pcb_port_{cid}_{pad.number}"

            angle = math.radians(comp.rotation + (pad.rotation or 0))
            rx = pad.x * math.cos(angle) - pad.y * math.sin(angle)
            ry = pad.x * math.sin(angle) + pad.y * math.cos(angle)

            abs_x = round(comp.x + rx, 4)
            abs_y = round(comp.y + ry, 4)
            pad_layers = [_layer_to_ts(l) for l in (pad.layers or ["F.Cu"]) if _layer_to_ts(l) in ("top", "bottom")] or ["top"]

            elements.append({
                "type": "source_port",
                "source_port_id": sp_id,
                "source_component_id": source_id,
                "name": f"{comp.ref}.{pad.number}",
                "pin_number": int(pad.number) if pad.number.isdigit() else pad_index,
            })

            elements.append({
                "type": "pcb_port",
                "pcb_port_id": pp_id,
                "source_port_id": sp_id,
                "pcb_component_id": pcb_id,
                "x": abs_x,
                "y": abs_y,
                "layers": pad_layers,
            })

            # Physical pad element — tscircuit's PCB viewer draws copper from
            # `pcb_smtpad` (SMD) and `pcb_plated_hole` (thru-hole). Without one
            # of these the board renders blank even though ports/nets exist.
            is_tht = (pad.type == "tht") or (pad.drill is not None)
            if is_tht:
                drill = pad.drill if pad.drill else min(pad.width, pad.height) * 0.5
                outer = max(pad.width, pad.height)
                elements.append({
                    "type": "pcb_plated_hole",
                    "shape": "circle",
                    "pcb_plated_hole_id": f"pcb_plated_hole_{cid}_{pad.number}",
                    "x": abs_x,
                    "y": abs_y,
                    "outer_diameter": round(outer, 4),
                    "hole_diameter": round(drill, 4),
                    "layers": ["top", "bottom"],
                    "pcb_component_id": pcb_id,
                    "pcb_port_id": pp_id,
                    "port_hints": [pad.number],
                })
            else:
                # Map PadDef shapes onto the pcb_smtpad shapes the viewer
                # understands. "oval" has no exact match -> fall back to rect.
                ts_shape = pad.shape if pad.shape in ("rect", "circle") else "rect"
                elements.append({
                    "type": "pcb_smtpad",
                    "shape": ts_shape,
                    "pcb_smtpad_id": f"pcb_smtpad_{cid}_{pad.number}",
                    "x": abs_x,
                    "y": abs_y,
                    "width": round(pad.width, 4),
                    "height": round(pad.height, 4),
                    "layer": pad_layers[0] if pad_layers else "top",
                    "pcb_component_id": pcb_id,
                    "pcb_port_id": pp_id,
                    "port_hints": [pad.number],
                })

    elements.append({
        "type": "pcb_silkscreen_rect",
        "pcb_silkscreen_rect_id": "pcb_silkscreen_board_outline",
        "layer": "top",
        "center": {"x": cx, "y": cy},
        "width": round(max(xs) - min(xs), 4) or 50,
        "height": round(max(ys) - min(ys), 4) or 40,
        "stroke_width": 0.1,
    })

    net_port_map: dict[str, list[str]] = {}
    for net_entry in model.nets:
        name = net_entry.get("name", "")
        if not name:
            continue
        port_ids: list[str] = []
        for pin_key in net_entry.get("pins", []):
            ref, _, pnum = pin_key.partition(":")
            cid_key = _sanitize_id(ref)
            pp_id = f"pcb_port_{cid_key}_{pnum}"
            sp_id = f"source_port_{cid_key}_{pnum}"
            if any(e.get("pcb_port_id") == pp_id for e in elements):
                port_ids.append(sp_id)
        if port_ids:
            net_port_map[name] = port_ids

    trace_port_map: dict[str, list[str]] = {}
    for trace in model.traces:
        net_name = trace.net
        if net_name not in trace_port_map:
            trace_port_map[net_name] = list(net_port_map.get(net_name, []))

    all_net_names: set[str] = set()
    for name in net_port_map:
        all_net_names.add(name)
    for t in model.traces:
        if t.net:
            all_net_names.add(t.net)

    net_index = 0
    for name in sorted(all_net_names):
        net_index += 1
        safe_name = _sanitize_id(name)
        net_id = f"source_net_{safe_name}"
        trace_id = f"source_trace_{safe_name}"

        elements.append({
            "type": "source_net",
            "source_net_id": net_id,
            "name": name,
        })

        connected_ports = net_port_map.get(name, [])
        elements.append({
            "type": "source_trace",
            "source_trace_id": trace_id,
            "connected_source_port_ids": connected_ports,
            "connected_source_net_ids": [net_id],
        })

    trace_index = 0
    for trace in model.traces:
        trace_index += 1
        safe_net = _sanitize_id(trace.net or f"trace_{trace_index}")
        source_trace_id = f"source_trace_{safe_net}"

        path = trace.path
        if not path:
            continue

        route: list[dict[str, Any]] = []
        for pt in path:
            route.append({
                "route_type": "wire",
                "x": round(pt[0], 4),
                "y": round(pt[1], 4),
                "width": round(trace.width, 4),
                "layer": _layer_to_ts(trace.layer),
            })

        if trace.via:
            vx, vy = trace.via
            route.append({
                "route_type": "via",
                "x": round(vx, 4),
                "y": round(vy, 4),
                "from_layer": _layer_to_ts(trace.layer),
                "to_layer": "bottom" if _layer_to_ts(trace.layer) == "top" else "top",
                "outer_diameter": 0.6,
                "hole_diameter": 0.3,
            })

        elements.append({
            "type": "pcb_trace",
            "pcb_trace_id": f"pcb_trace_{trace_index}",
            "source_trace_id": source_trace_id if any(e.get("source_trace_id") == source_trace_id for e in elements) else None,
            "route": route,
        })

    via_index = 0
    for via in model.vias:
        via_index += 1
        elements.append({
            "type": "pcb_via",
            "pcb_via_id": f"pcb_via_{via_index}",
            "x": round(via.x, 4),
            "y": round(via.y, 4),
            "outer_diameter": round(via.diameter, 4),
            "hole_diameter": round(via.drill, 4),
            "layers": [_layer_to_ts(l) for l in (via.layers or ["F.Cu", "B.Cu"])],
        })

    return elements
