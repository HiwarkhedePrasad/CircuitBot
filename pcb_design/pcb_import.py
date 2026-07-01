"""KiCad .kicad_pcb file importer.

Reads a .kicad_pcb board file using the vendored S-expression parser
and returns a BoardModel (the shared internal geometry model).
"""

from __future__ import annotations

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


def _get_str(n: list, idx: int = 1, default: str = "") -> str:
    return str(n[idx]) if len(n) > idx and n[idx] is not None else default


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
    pad = PadDef(number=str(node[1]), x=0, y=0, width=0, height=0)
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
            pad.layers = [str(s) for s in child[1:]]
        elif key == "drill":
            if isinstance(child[1], (int, float)):
                pad.drill = float(child[1])
        elif key == "type":
            pad.type = str(child[1]) if len(child) > 1 else "smd"
        elif key == "shape":
            pad.shape = str(child[1]) if len(child) > 1 else "rect"
    return pad


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
            layer = str(child[1]) if len(child) > 1 else "F.Cu"
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
            layers = [str(s) for s in child[1:]]
        elif key == "net":
            net = str(child[1]) if len(child) > 1 else ""
    return BoardVia(x=x, y=y, drill=drill, diameter=diameter, layers=layers, net=net)


def _parse_footprint(node: list) -> Optional[BoardComponent]:
    if not isinstance(node, list) or node[0] not in ("footprint", "module"):
        return None
    fp_str = str(node[1]) if len(node) > 1 else ""
    ref, value = "", ""
    x, y, rotation = 0.0, 0.0, 0.0
    layer = "F.Cu"
    pads = []
    at_node = _find_one(node, "at")
    if at_node:
        x = _get_float(at_node, 1)
        y = _get_float(at_node, 2)
        if len(at_node) > 3 and isinstance(at_node[3], (int, float)):
            rotation = float(at_node[3])
    layer_node = _find_one(node, "layer")
    if layer_node:
        layer = _get_str(layer_node, 1, "F.Cu")
    for prop in _find_all(node, "property"):
        if len(prop) > 2 and prop[1] == "Reference":
            ref = str(prop[2])
        elif len(prop) > 2 and prop[1] == "Value":
            value = str(prop[2])
    for pad_node in _find_all(node, "pad"):
        pad = _parse_pad(pad_node)
        if pad:
            pads.append(pad)
    return BoardComponent(
        ref=ref or fp_str, footprint=fp_str,
        x=x, y=y, rotation=rotation, layer=layer, value=value, pads=pads,
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
        layer = _get_str(layer_node, 1, "F.Cu")
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
            layer = _get_str(child, 1, "Edge.Cuts")
    if start and end and layer == "Edge.Cuts":
        return [start, end]
    return None


def _build_net_pin_map(ast: list) -> dict[int, list[str]]:
    """Walk the import AST and build ``net_id → [pin_key, ...]``.

    Each pad node inside a footprint may have a ``(net nid)`` child;
    this function collects all ``ref:padnum`` keys per net ID.
    """
    net_pins: dict[int, list[str]] = {}
    for fp_node in _find_all(ast, "footprint") + _find_all(ast, "module"):
        ref = ""
        for prop in _find_all(fp_node, "property"):
            if len(prop) > 2 and prop[1] == "Reference":
                ref = str(prop[2])
                break
        if not ref:
            fp_name = str(fp_node[1]) if len(fp_node) > 1 else "UNKNOWN"
            ref = fp_name
        for pad_node in _find_all(fp_node, "pad"):
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


def import_board(path: str) -> BoardModel:
    raw = Path(path).read_text(encoding="utf-8")
    ast = parse_sexp(raw)

    if not isinstance(ast, list) or ast[0] not in ("kicad_pcb", "pcbnew"):
        raise ValueError(f"Not a valid KiCad PCB file: {path}")

    model = BoardModel()

    version_node = _find_one(ast, "version")
    if version_node:
        model.version = str(version_node[1])

    nets = _find_all(ast, "net")
    parsed_nets = []
    net_id_to_name: dict[int, str] = {}
    for n in nets:
        if len(n) > 2:
            nid = int(n[1]) if isinstance(n[1], (int, float)) else 0
            name = str(n[2])
            net_id_to_name[nid] = name
            parsed_nets.append({"name": name, "pins": []})
    model.nets = parsed_nets

    net_pins = _build_net_pin_map(ast)

    # Populate pins from the net-pin map, using the canonical name key
    for net_entry in model.nets:
        name = net_entry["name"]
        for nid, pin_list in net_pins.items():
            if net_id_to_name.get(nid) == name:
                net_entry["pins"] = pin_list
                break

    for fp_node in _find_all(ast, "footprint"):
        comp = _parse_footprint(fp_node)
        if comp:
            model.components.append(comp)
    for mod_node in _find_all(ast, "module"):
        comp = _parse_footprint(mod_node)
        if comp:
            model.components.append(comp)

    for seg_node in _find_all(ast, "segment"):
        trace = _parse_trace(seg_node)
        if trace:
            model.traces.append(trace)

    for via_node in _find_all(ast, "via"):
        via = _parse_via(via_node)
        if via:
            model.vias.append(via)

    for zone_node in _find_all(ast, "zone"):
        zone = _parse_zone(zone_node)
        if zone:
            model.zones.append(zone)

    edge_pts = []
    for gr_node in _find_all(ast, "gr_line"):
        seg = _parse_gr_line(gr_node)
        if seg:
            edge_pts.extend(seg)
    if HAS_SHAPELY and len(edge_pts) >= 3:
        from shapely.geometry import Polygon as ShapelyPolygon
        model.outline = ShapelyPolygon(edge_pts)

    return model
