"""KiCad .kicad_pcb file importer.

Reads a .kicad_pcb board file using the vendored S-expression parser
and returns a BoardModel (the shared internal geometry model).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Optional

from kicad_rag.constants import UTILS_ROOT


def _syspath() -> None:
    p = str(UTILS_ROOT / "common")
    if p not in sys.path:
        sys.path.insert(0, p)


_syspath()
from sexpr import parse_sexp  # noqa: E402

from pcb_design.board_model import (
    BoardModel, BoardComponent, BoardTrace, BoardVia, BoardZone, PadDef,
)
from pcb_design.geometry import HAS_SHAPELY


def _find_all(node: list, *path: str) -> list[list]:
    """Recursively find all sub-lists matching a path of keywords."""
    results = []
    def _walk(n, depth=0):
        if not isinstance(n, list) or not n:
            return
        if depth == len(path):
            results.append(n)
            return
        if isinstance(n[0], str) and n[0] == path[depth]:
            _walk(n, depth + 1)
        for child in n[1:]:
            _walk(child, 0)
    _walk(node)
    return results


def _find_one(node: list, *path: str) -> Optional[list]:
    found = _find_all(node, *path)
    return found[0] if found else None


def _direct_children(node: list, *names: str) -> list[list]:
    """Return direct child lists whose head matches one of *names*."""
    wanted = set(names)
    results: list[list] = []
    for child in node[1:]:
        if isinstance(child, list) and child and isinstance(child[0], str) and child[0] in wanted:
            results.append(child)
    return results


def _get_str(n: list, idx: int = 1, default: str = "") -> str:
    return str(n[idx]) if len(n) > idx and n[idx] is not None else default


def _normalize_layer_name(value: Any, default: str = "F.Cu") -> str:
    raw = str(value if value is not None else default).strip().strip('"').strip("'")
    aliases = {
        "front_c": "F.Cu",
        "front_copper": "F.Cu",
        "f.cu": "F.Cu",
        "f_cu": "F.Cu",
        "top": "F.Cu",
        "top_copper": "F.Cu",
        "back_c": "B.Cu",
        "back_copper": "B.Cu",
        "b.cu": "B.Cu",
        "b_cu": "B.Cu",
        "bottom": "B.Cu",
        "bottom_copper": "B.Cu",
    }
    return aliases.get(raw.lower(), raw or default)


def _get_float(n: list, idx: int = 1, default: float = 0.0) -> float:
    if len(n) > idx and isinstance(n[idx], (int, float)):
        return float(n[idx])
    return default


def _parse_layer_id(name: str) -> int:
    layer_map = {
        "F.Cu": 0, "B.Cu": 31, "F.SilkS": 36, "B.SilkS": 32,
        "F.Mask": 35, "B.Mask": 33, "F.Paste": 34, "B.Paste": 32,
        "F.Fab": 30, "B.Fab": 29, "Edge.Cuts": 37, "Margin": 38,
        "F.CrtYd": 39, "B.CrtYd": 40, "Dwgs.User": 41, "Cmts.User": 42,
        "Eco1.User": 43, "Eco2.User": 44,
    }
    return layer_map.get(name, 0)


def _parse_pad(node: list) -> Optional[PadDef]:
    """Parse a pad node from a KiCad S-expression."""
    if not isinstance(node, list) or len(node) < 2:
        return None
    pad = PadDef(
        number=str(node[1]),
        x=0,
        y=0,
        width=0,
        height=0,
        type=str(node[2]) if len(node) > 2 else "smd",
        shape=str(node[3]) if len(node) > 3 else "rect",
    )
    for child in node[2:]:
        if not isinstance(child, list):
            continue
        key = child[0]
        if key == "at":
            pad.x = _get_float(child, 1)
            pad.y = _get_float(child, 2)
            if len(child) > 3 and isinstance(child[3], (int, float)):
                pad.rotation = float(child[3])
        elif key == "size":
            pad.width = _get_float(child, 1)
            pad.height = _get_float(child, 2)
        elif key == "layers":
            pad.layers = [_normalize_layer_name(s) for s in child[1:]]
        elif key == "drill":
            if len(child) > 1:
                if isinstance(child[1], (int, float)):
                    pad.drill = float(child[1])
                elif child[1] == "oval" and len(child) > 2:
                    # KiCad order: (drill oval X Y) where X=width, Y=height
                    if isinstance(child[2], (int, float)):
                        pad.drill = float(child[2])
                    if len(child) > 3 and isinstance(child[3], (int, float)):
                        pad.drill_width = float(child[3])
            offset_node = _find_one(child, "offset")
            if offset_node:
                pad.drill_offset_x = _get_float(offset_node, 1)
                pad.drill_offset_y = _get_float(offset_node, 2)
        elif key == "roundrect_rratio":
            pad.roundrect_rratio = float(child[1])
        elif key == "rect_delta":
            pad.rect_delta_x = _get_float(child, 1)
            pad.rect_delta_y = _get_float(child, 2)
    return pad


def _parse_fp_graphic(node: list) -> Optional[dict]:
    if not isinstance(node, list) or not node:
        return None
    kind = node[0]
    if kind not in ("fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly"):
        return None
    item = {"kind": kind, "layer": "F.SilkS", "width": 0.15}
    points = []
    for child in node[1:]:
        if not isinstance(child, list) or not child:
            continue
        key = child[0]
        if key in ("start", "end", "center", "mid"):
            item[key] = {"x": _get_float(child, 1), "y": _get_float(child, 2)}
        elif key == "pts":
            for pt in child[1:]:
                if isinstance(pt, list) and pt and pt[0] == "xy":
                    points.append({"x": _get_float(pt, 1), "y": _get_float(pt, 2)})
        elif key == "layer":
            item["layer"] = _normalize_layer_name(_get_str(child, 1, "F.SilkS"), "F.SilkS")
        elif key == "stroke":
            width_node = _find_one(child, "width")
            if width_node:
                item["width"] = _get_float(width_node, 1, 0.15)
            type_node = _find_one(child, "type")
            if type_node:
                item["line_style"] = _get_str(type_node, 1, "solid")
        elif key == "width":
            item["width"] = _get_float(child, 1, 0.15)
        elif key == "fill":
            # Handle both (fill yes/no/solid/none) and (fill (type solid/none))
            raw_fill = _get_str(child, 1, "none")
            type_node = _find_one(child, "type")
            if type_node:
                raw_fill = _get_str(type_node, 1, raw_fill)
            # Normalize: 'yes' and 'solid' both mean filled
            if raw_fill in ("yes", "solid"):
                item["fill"] = "solid"
            else:
                item["fill"] = "none"
    if points:
        item["points"] = points
    return item



def _parse_property_text(node: list) -> Optional[dict]:
    if not isinstance(node, list) or len(node) < 3 or node[0] not in ("property", "fp_text"):
        return None
    item = {
        "kind": "property" if node[0] == "property" else "fp_text",
        "name": str(node[1]),
        "text": str(node[2]),
        "layer": "F.SilkS",
        "x": 0.0,
        "y": 0.0,
        "rotation": 0.0,
        "size": 1.0,
        "hidden": False,
    }
    for child in node[3:]:
        if not isinstance(child, list) or not child:
            continue
        if child[0] == "at":
            item["x"] = _get_float(child, 1)
            item["y"] = _get_float(child, 2)
            if len(child) > 3 and isinstance(child[3], (int, float)):
                item["rotation"] = float(child[3])
        elif child[0] == "layer":
            item["layer"] = _normalize_layer_name(_get_str(child, 1, "F.SilkS"), "F.SilkS")
        elif child[0] == "effects":
            font_node = _find_one(child, "font")
            if font_node:
                size_node = _find_one(font_node, "size")
                if size_node:
                    item["size"] = _get_float(size_node, 1, 1.0)
        elif child[0] == "hide" or (isinstance(child, list) and child and child[0] == "hide"):
            item["hidden"] = True
    return item


def _parse_trace(node: list) -> Optional[BoardTrace]:
    if not isinstance(node, list) or node[0] not in ("segment", "arc"):
        return None
    start, end = None, None
    layer, net, width = "F.Cu", "", 0.254
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        key = child[0]
        if key == "start":
            start = (_get_float(child, 1), _get_float(child, 2))
        elif key == "end":
            end = (_get_float(child, 1), _get_float(child, 2))
        elif key == "layer":
            layer = _normalize_layer_name(child[1] if len(child) > 1 else "F.Cu")
        elif key == "net":
            net = str(child[1]) if len(child) > 1 else ""
        elif key == "width":
            width = _get_float(child, 1, 0.254)
    if start and end:
        return BoardTrace(net=net, layer=layer, width=width, path=[start, end])
    return None


def _parse_via(node: list) -> Optional[BoardVia]:
    if not isinstance(node, list) or node[0] != "via":
        return None
    x, y, drill, diameter = 0.0, 0.0, 0.3, 0.6
    layers = ["F.Cu", "B.Cu"]
    net = ""
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        key = child[0]
        if key == "at":
            x = _get_float(child, 1)
            y = _get_float(child, 2)
        elif key == "drill":
            drill = _get_float(child, 1, 0.3)
        elif key == "size":
            diameter = _get_float(child, 1, 0.6)
        elif key == "layers":
            layers = [_normalize_layer_name(s) for s in child[1:]]
        elif key == "net":
            net = str(child[1]) if len(child) > 1 else ""
    return BoardVia(x=x, y=y, drill=drill, diameter=diameter, layers=layers, net=net)


def _parse_3d_model(node: list) -> Optional[dict]:
    """Extract (model "${KICAD_3DMODEL_DIR}/path/file.step" (offset ...) (scale ...) (rotate ...))"""
    try:
        if not isinstance(node, list) or node[0] != "model":
            return None
        raw_path = str(node[1]) if len(node) > 1 else ""
        path = raw_path.replace("${KICAD10_3DMODEL_DIR}/", "").replace("${KICAD_3DMODEL_DIR}/", "")
        offset = (0.0, 0.0, 0.0)
        scale = (1.0, 1.0, 1.0)
        rotate = (0.0, 0.0, 0.0)
        for child in node[2:]:
            if not isinstance(child, list) or not child:
                continue
            xyz_node = _find_one(child, "xyz")
            if not xyz_node:
                continue
            vals = (
                _get_float(xyz_node, 1, 0.0),
                _get_float(xyz_node, 2, 0.0),
                _get_float(xyz_node, 3, 0.0),
            )
            if child[0] == "offset":
                offset = vals
            elif child[0] == "scale":
                scale = vals
            elif child[0] == "rotate":
                rotate = vals
        return {"path": path, "offset": offset, "scale": scale, "rotate": rotate}
    except Exception as e:
        return None


def _parse_footprint(node: list) -> Optional[BoardComponent]:
    if not isinstance(node, list) or node[0] not in ("footprint", "module"):
        return None
    fp_str = str(node[1]) if len(node) > 1 else ""
    ref, value = "", ""
    x, y, rotation = 0.0, 0.0, 0.0
    layer = "F.Cu"
    pads = []
    graphics = []
    model_3d = None
    for child in node[2:]:
        if not isinstance(child, list) or not child:
            continue
        if child[0] == "at":
            x = _get_float(child, 1)
            y = _get_float(child, 2)
            if len(child) > 3 and isinstance(child[3], (int, float)):
                rotation = float(child[3])
            continue
        if child[0] == "layer":
            layer = _normalize_layer_name(_get_str(child, 1, "F.Cu"))
            continue
        if child[0] in ("property", "fp_text"):
            if len(child) > 2 and child[1] == "Reference":
                ref = str(child[2])
            elif len(child) > 2 and child[1] == "Value":
                value = str(child[2])
            text_item = _parse_property_text(child)
            if text_item:
                graphics.append(text_item)
            continue
        if child[0] == "pad":
            pad = _parse_pad(child)
            if pad:
                pads.append(pad)
            continue
        if child[0] == "model":
            model_3d = _parse_3d_model(child)
            continue
        graphic = _parse_fp_graphic(child)
        if graphic:
            graphics.append(graphic)
    return BoardComponent(
        ref=ref or fp_str, footprint=fp_str,
        x=x, y=y, rotation=rotation, layer=layer, value=value, pads=pads, graphics=graphics,
        model_3d_path=model_3d["path"] if model_3d else None,
        model_3d_offset=model_3d["offset"] if model_3d else (0.0, 0.0, 0.0),
        model_3d_scale=model_3d["scale"] if model_3d else (1.0, 1.0, 1.0),
        model_3d_rotate=model_3d["rotate"] if model_3d else (0.0, 0.0, 0.0),
    )


def _parse_zone(node: list) -> Optional[BoardZone]:
    if not isinstance(node, list) or node[0] != "zone":
        return None
    net, layer = "", "F.Cu"
    priority = 0
    net_node = _find_one(node, "net")
    if net_node:
        net = _get_str(net_node, 1)
    layer_node = _find_one(node, "layer")
    if layer_node:
        layer = _normalize_layer_name(_get_str(layer_node, 1, "F.Cu"))
    priority_node = _find_one(node, "priority")
    if priority_node:
        priority = int(_get_float(priority_node, 1, 0))
    polygon = None
    if HAS_SHAPELY:
        from shapely.geometry import Polygon as ShapelyPolygon
        pts_node = _find_one(node, "pts")
        if pts_node:
            xy = _find_one(pts_node, "xy")
            if xy and isinstance(xy, list) and len(xy) > 2:
                coords = []
                for i in range(1, len(xy)):
                    if isinstance(xy[i], list) and len(xy[i]) >= 3:
                        coords.append((float(xy[i][1]), float(xy[i][2])))
                if coords:
                    polygon = ShapelyPolygon(coords)
    return BoardZone(net=net, layer=layer, polygon=polygon, priority=priority)


def _parse_gr_line(node: list) -> Optional[list[tuple[float, float]]]:
    if not isinstance(node, list) or node[0] not in ("gr_line", "gr_arc", "gr_circle"):
        return None
    start, end = None, None
    layer = "Edge.Cuts"
    for child in node[1:]:
        if not isinstance(child, list):
            continue
        key = child[0]
        if key == "start":
            start = (_get_float(child, 1), _get_float(child, 2))
        elif key == "end":
            end = (_get_float(child, 1), _get_float(child, 2))
        elif key == "layer":
            layer = _normalize_layer_name(_get_str(child, 1, "Edge.Cuts"), "Edge.Cuts")
    if start and end and layer == "Edge.Cuts":
        return [start, end]
    return None


def _parse_outline_segment(node: list) -> Optional[dict]:
    if not isinstance(node, list) or not node:
        return None
    if node[0] not in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly"):
        return None
    item = {"kind": node[0], "layer": "Edge.Cuts"}
    points = []
    for child in node[1:]:
        if not isinstance(child, list) or not child:
            continue
        key = child[0]
        if key in ("start", "end", "center", "mid"):
            item[key] = {"x": _get_float(child, 1), "y": _get_float(child, 2)}
        elif key == "pts":
            for pt in child[1:]:
                if isinstance(pt, list) and pt and pt[0] == "xy":
                    points.append({"x": _get_float(pt, 1), "y": _get_float(pt, 2)})
        elif key == "layer":
            item["layer"] = _normalize_layer_name(_get_str(child, 1, "Edge.Cuts"), "Edge.Cuts")
    if item.get("layer") != "Edge.Cuts":
        return None
    # Convert KiCad gr_arc format (start=center) to frontend format (three points on arc)
    if node[0] == "gr_arc" and "start" in item and "mid" in item and "end" in item:
        center = item.pop("start")  # KiCad start = center of arc
        mid = item["mid"]
        end = item["end"]
        angle_mid = math.atan2(mid["y"] - center["y"], mid["x"] - center["x"])
        angle_end = math.atan2(end["y"] - center["y"], end["x"] - center["x"])
        radius = math.hypot(end["x"] - center["x"], end["y"] - center["y"])
        angle_start = angle_end + 2 * (angle_mid - angle_end)
        item["start"] = {
            "x": round(center["x"] + radius * math.cos(angle_start), 4),
            "y": round(center["y"] + radius * math.sin(angle_start), 4),
        }
        item["center"] = center
    if points:
        item["points"] = points
    return item


def _build_net_pin_map(footprint_nodes: list[list]) -> dict[int, list[str]]:
    """Build ``net_id -> [pin_key, ...]`` from footprint/module nodes."""
    net_pins: dict[int, list[str]] = {}
    for fp_node in footprint_nodes:
        ref = ""
        for prop in _direct_children(fp_node, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = str(prop[2])
                break
        if not ref:
            fp_name = str(fp_node[1]) if len(fp_node) > 1 else "UNKNOWN"
            ref = fp_name
        for pad_node in _direct_children(fp_node, "pad"):
            pnum = str(pad_node[1]) if len(pad_node) > 1 else "0"
            net_id = None
            for child in pad_node[2:]:
                if isinstance(child, list) and child[0] == "net":
                    raw = child[1] if len(child) > 1 else None
                    if isinstance(raw, (int, float)):
                        net_id = int(raw)
                    break
            if net_id is not None:
                pin_key = f"{ref}:{pnum}"
                net_pins.setdefault(net_id, []).append(pin_key)
    return net_pins


def _collect_board_nodes(ast: list) -> dict[str, list[list]]:
    """Collect top-level board items in one pass over the KiCad AST."""
    buckets: dict[str, list[list]] = {
        "net": [],
        "footprint": [],
        "module": [],
        "segment": [],
        "arc": [],
        "via": [],
        "zone": [],
        "gr_line": [],
        "gr_arc": [],
        "gr_rect": [],
        "gr_circle": [],
        "gr_poly": [],
    }
    for child in ast[1:]:
        if not isinstance(child, list) or not child:
            continue
        key = child[0]
        if key in buckets:
            buckets[key].append(child)
    return buckets


def import_board(path: str) -> BoardModel:
    raw = Path(path).read_text(encoding="utf-8")
    ast = parse_sexp(raw)

    if not isinstance(ast, list) or ast[0] not in ("kicad_pcb", "pcbnew"):
        raise ValueError(f"Not a valid KiCad PCB file: {path}")

    model = BoardModel()
    model._pcbnew_content = raw

    version_node = _find_one(ast, "version")
    if version_node:
        model.version = str(version_node[1])

    nodes = _collect_board_nodes(ast)

    nets = nodes["net"]
    parsed_nets = []
    net_id_to_name: dict[int, str] = {}
    for n in nets:
        if len(n) > 2:
            nid = int(n[1]) if isinstance(n[1], (int, float)) else 0
            name = str(n[2])
            net_id_to_name[nid] = name
            parsed_nets.append({"name": name, "pins": []})
    model.nets = parsed_nets

    footprint_nodes = nodes["footprint"] + nodes["module"]
    net_pins = _build_net_pin_map(footprint_nodes)

    # Populate pins from the net-pin map, using the canonical name key
    for net_entry in model.nets:
        name = net_entry["name"]
        for nid, pin_list in net_pins.items():
            if net_id_to_name.get(nid) == name:
                net_entry["pins"] = pin_list
                break

    for fp_node in footprint_nodes:
        comp = _parse_footprint(fp_node)
        if comp:
            model.components.append(comp)

    for seg_node in nodes["segment"] + nodes["arc"]:
        trace = _parse_trace(seg_node)
        if trace:
            model.traces.append(trace)

    for via_node in nodes["via"]:
        via = _parse_via(via_node)
        if via:
            model.vias.append(via)

    for zone_node in nodes["zone"]:
        zone = _parse_zone(zone_node)
        if zone:
            model.zones.append(zone)

    edge_pts = []
    outline_segments = []
    for gr_node in nodes["gr_line"]:
        seg = _parse_gr_line(gr_node)
        if seg:
            edge_pts.extend(seg)
    for gr_node in nodes["gr_line"] + nodes["gr_arc"] + nodes["gr_rect"] + nodes["gr_circle"] + nodes["gr_poly"]:
        segment = _parse_outline_segment(gr_node)
        if segment:
            outline_segments.append(segment)
    if HAS_SHAPELY and len(edge_pts) >= 3:
        from shapely.geometry import Polygon as ShapelyPolygon
        model.outline = ShapelyPolygon(edge_pts)
    model.outline_segments = outline_segments

    return model
