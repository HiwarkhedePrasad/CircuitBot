"""KiCad PCB (.kicad_pcb) exporter.

Converts the agent's design state into a complete KiCad 8 compatible
PCB layout file with embedded footprints, net declarations, and routed
track segments.
"""
from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path

from kicad_rag.constants import FOOTPRINTS_ROOT, UTILS_ROOT

from pcb_design.geometry import DEFAULT_CLEARANCE

GRID = 1.27


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _q(s: str) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _snap(v: float) -> float:
    return round(v, 4)


def _syspath() -> None:
    p = str(UTILS_ROOT / "common")
    if p not in sys.path:
        sys.path.insert(0, p)


# ── S-expression serializer ────────────────────────────────────────────────────


def _serialize(node, indent: int = 0) -> str:
    """Serialize a parsed S-expression node back to KiCad-compatible text.

    First child is a bare keyword; subsequent string values are quoted.
    Layout: keyword + simple args on the first line, then each sub-list
    on its own indented line.
    """
    if not isinstance(node, list):
        if isinstance(node, str):
            return _q(node)
        return str(node)

    has_subs = any(isinstance(c, list) for c in node[1:])

    if not has_subs:
        # Single‑line: (keyword val1 val2 ...)
        parts = [_serialize_atom(node[0], is_first=True)]
        for c in node[1:]:
            parts.append(_serialize_atom(c, is_first=False))
        return "(" + " ".join(parts) + ")"

    # Multi‑line: (keyword val1
    #                (sub ...)
    #              )
    ind = "  " * indent
    inner = "  " * (indent + 1)
    first = _serialize_atom(node[0], is_first=True)
    simple_args = " ".join(
        _serialize_atom(c, is_first=False)
        for c in node[1:] if not isinstance(c, list)
    )
    lines = [f"({first} {simple_args}" if simple_args else f"({first}"]
    for c in node[1:]:
        if isinstance(c, list):
            lines.append(f"{inner}{_serialize(c, indent + 1)}")
    lines.append(f"{ind})")
    return "\n".join(lines)


_BARE_KEYWORD_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _needs_quoting(s: str) -> bool:
    """KiCad 10 bare-keyword rule: ``yes``, ``smd``, ``thru_hole`` stay unquoted;
    strings with dots, dashes, asterisks, spaces, or starting with a digit are quoted."""
    return not bool(_BARE_KEYWORD_RE.match(s))


def _serialize_atom(val, is_first: bool = False) -> str:
    if isinstance(val, str):
        return val if is_first else (_q(val) if _needs_quoting(val) else val)
    if isinstance(val, float):
        return _fmt(val)
    return str(val)


# ── Net map ────────────────────────────────────────────────────────────────────


def _build_pin_to_net(design: dict) -> dict[str, str]:
    pin_to_net: dict[str, str] = {}
    for net in design.get("nets", []):
        name = net.get("net", "")
        for pin in net.get("pins", []):
            pin_to_net[pin] = name
    for pp in design.get("power_pins", []):
        pin = pp.get("pin", "")
        net_name = pp.get("net", "")
        if pin and net_name:
            pin_to_net[pin] = net_name
    return pin_to_net


def _build_net_index(pin_to_net: dict[str, str]) -> dict[str, int]:
    used = set(pin_to_net.values()) - {""}
    ordered: list[str] = []
    for priority in ("GND",):
        if priority in used:
            ordered.append(priority)
            used.discard(priority)
    ordered.extend(sorted(used))
    return {name: i + 1 for i, name in enumerate(ordered)}


# ── Footprint embedding ──────────────────────────────────────────────────────


def _fp_path_for(fp_str: str) -> Path:
    cat, _, name = fp_str.partition(":")
    return FOOTPRINTS_ROOT / f"{cat}.pretty" / f"{name}.kicad_mod"


def _embed_footprint(fp_str: str, ref_des: str, value: str,
                     x: float, y: float,
                     rotation: float = 0,
                     pin_to_net: dict = None,
                     net_index: dict = None) -> str:
    if pin_to_net is None: pin_to_net = {}
    if net_index is None: net_index = {}
    _syspath()
    from sexpr import parse_sexp  # noqa: E402

    mod_path = _fp_path_for(fp_str)
    if not mod_path.is_file():
        return _minimal_footprint(fp_str, ref_des, value, x, y, rotation)

    try:
        raw = mod_path.read_text(encoding="utf-8")
        ast = parse_sexp(raw)

        # Ensure root keyword is "footprint" (KiCad 10 native; KiCad 7/8 also accept it)
        if ast and isinstance(ast, list) and len(ast) > 1:
            ast[0] = "footprint"
            ast[1] = fp_str

        # Remove any top-level tokens that would conflict with our placement:
        # at (replaced below), tedit, tstamp — keep version/generator/generator_version
        filtered = [child for child in ast
                    if not (isinstance(child, list)
                            and child[0] in ("at", "tedit", "tstamp", "uuid"))]
        filtered.insert(2, ["at", x, y, rotation])
        ast = filtered

        for child in ast:
            if isinstance(child, list) and len(child) >= 3:
                if child[0] == "property":
                    if child[1] == "Reference":
                        child[2] = ref_des
                    elif child[1] == "Value":
                        child[2] = value
                elif child[0] == "pad":
                    pad_num = str(child[1])
                    pin_key = f"{ref_des}:{pad_num}"
                    net_name = pin_to_net.get(pin_key)
                    if net_name:
                        nid = net_index.get(net_name, 0)
                        if nid > 0:
                            child[:] = [c for c in child if not (isinstance(c, list) and c and c[0] == "net")]
                            child.append(["net", nid, net_name])

        return _serialize(ast)
    except Exception as exc:
        print(f"  ! footprint embed failed {fp_str}: {exc}", file=sys.stderr)
        return _minimal_footprint(fp_str, ref_des, value, x, y, rotation)


def _minimal_footprint(fp_str: str, ref_des: str, value: str,
                       x: float, y: float,
                       rotation: float = 0) -> str:
    return _serialize([
        "footprint", fp_str,
        ["version", 20260206],
        ["generator", "circuitbot"],
        ["layer", "F.Cu"],
        ["at", x, y, rotation],
        ["descr", fp_str],
        ["tags", ""],
        ["property", "Reference", ref_des,
         ["at", x, y - 2.54, 0],
         ["layer", "F.SilkS"],
         ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
        ["property", "Value", value,
         ["at", x, y + 2.54, 0],
         ["layer", "F.Fab"],
         ["effects", ["font", ["size", 1, 1], ["thickness", 0.15]]]],
    ])


# ── Track segments ──────────────────────────────────────────────────────────────


def _simplify_path(points: list) -> list:
    if len(points) < 3:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        x0, y0 = out[-1]["x"], out[-1]["y"]
        x1, y1 = points[i]["x"], points[i]["y"]
        x2, y2 = points[i + 1]["x"], points[i + 1]["y"]
        if (x1 - x0) * (y2 - y1) != (y1 - y0) * (x2 - x1):
            out.append(points[i])
    out.append(points[-1])
    return out


# ── Main entry point ──────────────────────────────────────────────────────────


def generate_kicad_pcb(design: dict) -> str:
    # Prefer pcbnew-generated content when available
    board_model = design.get("board_model")
    if board_model and board_model.get("_pcbnew_content"):
        return board_model["_pcbnew_content"]

    # Fall back to BoardModel export
    if board_model:
        try:
            return _generate_from_board_model(board_model)
        except Exception as e:
            print(f"BoardModel export failed, falling back: {e}")

    comps = design.get("selected_components", [])
    placements = {p["ref_des"]: p for p in design.get("component_placements", [])}
    wires = design.get("wire_paths", [])

    # ── 1. Build net map ──────────────────────────────────────────────────
    pin_to_net = _build_pin_to_net(design)
    net_index = _build_net_index(pin_to_net)

    # Ensure every wire_path source pin has a net mapped
    for w in wires:
        src = w.get("source", "")
        if src and src not in pin_to_net:
            pin_to_net[src] = ""

    # ── 2. Build layers ──────────────────────────────────────────────────
    # KiCad layer numbers: 0=F.Cu, 31=B.Cu, 32-37 = tech layers
    layers = [
        (0, "F.Cu", "signal"),
        (31, "B.Cu", "signal"),
        (32, "B.SilkS", "user"),
        (33, "B.Mask", "user"),
        (34, "F.Paste", "user"),
        (35, "F.Mask", "user"),
        (36, "F.SilkS", "user"),
        (37, "Edge.Cuts", "user"),
    ]

    # ── 3. Assemble document ────────────────────────────────────────────
    out: list[str] = []
    out.append("(kicad_pcb (version 20260206) (generator \"circuitbot\") (generator_version \"1.0\")")

    # Layers section
    out.append("  (layers")
    for num, name, ltype in layers:
        out.append(f"    ({num} {_q(name)} {ltype})")
    out.append("  )")

    # Setup section
    out.append("  (setup")
    out.append("    (stackup")
    out.append(f"      (layer \"F.Cu\" (type \"copper\") (thickness 0.035))")
    out.append(f"      (layer \"B.Cu\" (type \"copper\") (thickness 0.035))")
    out.append("    )")
    out.append("  )")

    # Net declarations (net 0 is always the unconnected net)
    out.append(f"  (net 0 \"\")")
    for net_name, nid in sorted(net_index.items(), key=lambda kv: kv[1]):
        out.append(f"  (net {nid} {_q(net_name)})")

    # Footprint definitions (direct children of root, no wrapper)
    for comp in comps:
        ref = comp["ref_des"]
        place = placements.get(ref)
        if not place:
            continue
        fp_str = comp.get("footprint", "")
        if not fp_str:
            continue
        x = _snap(place.get("x", 0))
        y = _snap(place.get("y", 0))
        rot = place.get("rotation", 0)
        value = comp.get("id_str", "").rpartition(":")[2] or ref
        fp_sexpr = _embed_footprint(fp_str, ref, value, x, y, rot, pin_to_net, net_index)
        for line in fp_sexpr.strip().split("\n"):
            out.append(f"  {line}")

    # Traces (segments) are intentionally omitted here.
    # The agent should not autoroute the PCB using schematic wire_paths.
    # Traces must be routed entirely manually by the user in the PCB editor.

    out.append(")")
    return "\n".join(out) + "\n"


def _generate_from_board_model(board_model: dict) -> str:
    """Export from BoardModel dict (produced by pcb_design.board_model.BoardModel.to_dict())."""
    from pcb_design.board_model import BoardModel as BM
    model = BM.from_dict(board_model)

    layers = [
        (0, "F.Cu", "signal"),
        (31, "B.Cu", "signal"),
        (32, "B.SilkS", "user"),
        (33, "B.Mask", "user"),
        (34, "F.Paste", "user"),
        (35, "F.Mask", "user"),
        (36, "F.SilkS", "user"),
        (37, "Edge.Cuts", "user"),
    ]

    # Build net index and pin_to_net
    net_index = {"": 0}
    pin_to_net = {}
    for net in model.nets:
        name = net.get("name", "") or net.get("net", "")
        if not name:
            continue
        if name not in net_index:
            net_index[name] = len(net_index)
        for pin in net.get("pins", []):
            pin_to_net[pin] = name

    out = []
    out.append("(kicad_pcb (version 20260206) (generator \"circuitbot\") (generator_version \"1.0\")")

    out.append("  (layers")
    for num, name, ltype in layers:
        out.append(f"    ({num} {_q(name)} {ltype})")
    out.append("  )")

    out.append("  (setup")
    out.append("    (stackup")
    out.append("      (layer \"F.Cu\" (type \"copper\") (thickness 0.035))")
    out.append("      (layer \"B.Cu\" (type \"copper\") (thickness 0.035))")
    out.append("    )")
    out.append("  )")

    for net_name, nid in sorted(net_index.items(), key=lambda kv: kv[1]):
        out.append(f"  (net {nid} {_q(net_name)})")

    for comp in model.components:
        fp_str = comp.footprint
        if not fp_str:
            continue
        x = _snap(comp.x)
        y = _snap(comp.y)
        ref = comp.ref
        value = comp.value or ref
        fp_sexpr = _embed_footprint(fp_str, ref, value, x, y, comp.rotation, pin_to_net, net_index)
        for line in fp_sexpr.strip().split("\n"):
            out.append(f"  {line}")

    for trace in model.traces:
        pts = [(p[0], p[1]) for p in trace.path] if isinstance(trace.path[0], tuple) else [
            (p["x"], p["y"]) for p in trace.path
        ]
        pts_simple = _simplify_path([{"x": p[0], "y": p[1]} for p in pts])
        if len(pts_simple) < 2:
            continue
        nid = net_index.get(trace.net, 0)
        for i in range(len(pts_simple) - 1):
            x1 = _snap(pts_simple[i]["x"])
            y1 = _snap(pts_simple[i]["y"])
            x2 = _snap(pts_simple[i + 1]["x"])
            y2 = _snap(pts_simple[i + 1]["y"])
            if x1 == x2 and y1 == y2:
                continue
            out.append(
                f"  (segment (start {_fmt(x1)} {_fmt(y1)})"
                f" (end {_fmt(x2)} {_fmt(y2)})"
                f" (width {_fmt(trace.width)}) (layer {_q(trace.layer)})"
                f" (net {nid}))"
            )

    for via in model.vias:
        out.append(
            f"  (via (at {_fmt(via.x)} {_fmt(via.y)})"
            f" (drill {_fmt(via.drill)})"
            f" (size {_fmt(via.diameter)})"
            f" (layers {_q(via.layers[0])} {_q(via.layers[1])})"
            f" (net {net_index.get(via.net, 0)}))"
        )

    for zone in model.zones:
        if zone.polygon is None or zone.polygon.is_empty:
            continue
        nid = net_index.get(zone.net, 0)
        coords = list(zone.polygon.exterior.coords) if zone.polygon.exterior else []
        if len(coords) < 3:
            continue
        out.append(f"  (zone (net {nid}) (net_name {_q(zone.net)}) (layer {_q(zone.layer)})")
        out.append(f"    (priority {zone.priority})")
        out.append(f"    (hatch edge 0.5)")
        out.append(f"    (connect_pads (clearance {_fmt(DEFAULT_CLEARANCE)}))")
        out.append(f"    (min_thickness {_fmt(DEFAULT_CLEARANCE)})")
        out.append(f"    (fill yes (arc_segments 32) (thermal_gap 0.254) (thermal_bridge_width 0.254))")
        out.append(f"    (polygon")
        out.append(f"      (pts")
        for cx, cy in coords:
            out.append(f"        (xy {_fmt(cx)} {_fmt(cy)})")
        out.append(f"      )")
        out.append(f"    )")
        out.append(f"  )")

    out.append(")")
    return "\n".join(out) + "\n"
