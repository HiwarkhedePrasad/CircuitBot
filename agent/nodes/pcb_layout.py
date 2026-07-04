"""PCB layout node — placement + ratsnest only (no autorouting).

Pipeline:
  1. Force-directed PCB component placement (graph-driven clustering).
  2. Build BoardModel from state + placement results.
  3. Build model.nets from netlist as single source of truth.
  4. Compute ratsnest (airwire guide lines).
  5. Emit agent:pcb_ready + agent:done events.
"""

from collections import defaultdict

from agent.utils import _emit, emit_assistant_message, emit_tool_event

from pcb_design.board_model import (
    BoardModel, BoardComponent, PadDef,
)
from pcb_design.placement import place_components
from pcb_design.geometry import board_outline_polygon, HAS_SHAPELY


def _pad_from_dict(pd: dict) -> PadDef:
    """Convert either database pad JSON or BoardModel-style pad JSON."""
    return PadDef(
        number=str(pd.get("number", "")),
        x=float(pd.get("x", 0) or 0),
        y=float(pd.get("y", 0) or 0),
        width=float(pd.get("width", pd.get("sx", 1)) or 1),
        height=float(pd.get("height", pd.get("sy", 1)) or 1),
        shape=pd.get("shape", "rect"),
        type=pd.get("type", "smd"),
        rotation=float(pd.get("rotation", pd.get("ox", 0)) or 0),
        drill=pd.get("drill"),
        layers=pd.get("layers", ["F.Cu", "F.Mask", "F.Paste"]),
    )


def _load_footprint_component(comp: dict) -> BoardComponent | None:
    """Parse the KiCad footprint file so the PCB view gets pads and graphics."""
    footprint = comp.get("footprint", "")
    if not footprint:
        return None
    try:
        from kicad_rag.store import footprint_path_for
        from pcb_design.pcb_import import _parse_footprint, parse_sexp

        fp_path = footprint_path_for(footprint)
        if not fp_path.is_file():
            return None
        ast = parse_sexp(fp_path.read_text(encoding="utf-8"))
        if isinstance(ast, list) and ast and ast[0] not in ("footprint", "module"):
            ast[0] = "footprint"
        return _parse_footprint(ast)
    except Exception:
        return None


def _hydrate_component_for_pcb(comp: dict) -> tuple[list[PadDef], list[dict], str]:
    """Return pads, footprint graphics, and footprint name for a selected component."""
    hydrated = dict(comp)
    if (not hydrated.get("footprint") or not hydrated.get("pads")) and hydrated.get("id_str"):
        try:
            from agent.tools import fetch_footprint
            info = fetch_footprint(hydrated["id_str"])
            if info:
                hydrated["footprint"] = hydrated.get("footprint") or info.get("footprint", "")
                hydrated["pads"] = hydrated.get("pads") or info.get("pads", [])
        except Exception:
            pass

    parsed_fp = _load_footprint_component(hydrated)
    if parsed_fp and parsed_fp.pads:
        return parsed_fp.pads, parsed_fp.graphics, hydrated.get("footprint", parsed_fp.footprint)

    pads = [_pad_from_dict(pd) for pd in hydrated.get("pads", [])]
    return pads, (parsed_fp.graphics if parsed_fp else []), hydrated.get("footprint", "")


def _build_nets_from_netlist(netlist: list[dict], pin_matrix: dict) -> list[dict]:
    """Build model.nets from netlist as the single source of truth.

    Groups all netlist entries by net name and collects unique pin keys
    for each net.  Falls back to grouping by signal name when a net
    entry has no *net* field.
    """
    net_groups: dict[str, set[str]] = defaultdict(set)
    for conn in netlist:
        src = conn.get("source", "")
        tgt = conn.get("target", "")
        net = conn.get("net", "")
        if not net:
            # Derive net name from the source pin name in pin_matrix
            if src in pin_matrix:
                net = pin_matrix[src].get("name", "")
            if not net and tgt in pin_matrix:
                net = pin_matrix[tgt].get("name", "")
            if not net:
                net = f"_signal_{src}_{tgt}"
        if src:
            net_groups[net].add(src)
        if tgt:
            net_groups[net].add(tgt)
    return [{"name": n, "pins": sorted(p)} for n, p in net_groups.items()]


def pcb_layout_node(state, config):
    _emit(config, "agent:thinking", {"message": "Placing components on PCB..."})
    emit_assistant_message(config, "Laying out components on the PCB...")
    emit_tool_event(config, "PCB Layout", "running", "Graph-driven placement...")

    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    power_labels = state.get("power_labels", [])

    if not comps:
        _emit(config, "agent:log", {"message": "No components for PCB layout."})
        return {}

    # ── 1. Build nets from netlist (single source of truth) ────────
    nets = _build_nets_from_netlist(netlist, pin_matrix)

    # ── 2. Graph-driven placement ──────────────────────────────────
    pcb_placements = place_components(comps, netlist, pin_matrix=pin_matrix)
    pcb_pos = {p["ref_des"]: (p["x"], p["y"], p.get("rotation", 0)) for p in pcb_placements}

    # ── 3. Build BoardModel ────────────────────────────────────────
    model = BoardModel(
        nets=nets,
        power_pins=power_pins,
        power_labels=power_labels,
    )

    missing_footprints = []
    missing_pads = []
    for comp in comps:
        ref = comp["ref_des"]
        pads, graphics, footprint = _hydrate_component_for_pcb(comp)
        if not footprint:
            missing_footprints.append(ref)
        if not pads:
            missing_pads.append(ref)
        bx, by, brot = pcb_pos.get(ref, (0, 0, 0))
        model.components.append(BoardComponent(
            ref=ref,
            footprint=footprint,
            x=bx, y=by,
            rotation=brot,
            value=comp.get("id_str", "").rpartition(":")[2] if comp else "",
            pads=pads,
            graphics=graphics,
            bbox=(0, 0, 10, 10),
        ))

    if missing_footprints:
        _emit(config, "agent:log", {
            "message": "  PCB warning: missing footprint for " + ", ".join(missing_footprints)
        })
    if missing_pads:
        _emit(config, "agent:log", {
            "message": "  PCB warning: no pads available for " + ", ".join(missing_pads)
        })

    if HAS_SHAPELY:
        model.outline = board_outline_polygon(
            [{"x": c.x, "y": c.y, "pads": [{"x": p.x, "y": p.y} for p in c.pads]}
             for c in model.components]
        )

    emit_tool_event(config, "PCB Layout", "running",
                    f"Placed {len(model.components)} components (graph-driven)")

    # ── 4. Compute ratsnest (airwire guide lines) ──────────────────
    board_dict = model.to_dict()
    try:
        from pcb_design.ratsnest import compute_ratsnest
        board_dict["ratsnest"] = compute_ratsnest(model)
    except Exception:
        board_dict["ratsnest"] = {}

    # ── 5. Emit final events ───────────────────────────────────────
    _emit(config, "agent:pcb_ready", {"board_model": board_dict})
    _emit(config, "agent:done", {
        "message": (f"Design complete: {len(model.components)} components. "
                    f"Ratsnest guide lines ready — route traces manually in the PCB viewer.")
    })
    emit_tool_event(config, "PCB Layout", "completed",
                    f"{len(model.components)} components placed — manual routing required")
    emit_assistant_message(config,
                           f"PCB complete — {len(model.components)} components placed. "
                           f"Use the PCB viewer to route traces manually.")

    return {"board_model": board_dict, "_board_model": board_dict}
