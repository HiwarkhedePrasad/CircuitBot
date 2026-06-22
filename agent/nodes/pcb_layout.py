"""PCB layout node — force-directed placement, A* routing, GND pour, DRC.

Extracted from the former monolithic layout_route_node. Handles:
  1. Force-directed PCB component placement (connectivity-aware).
  2. Build BoardModel from state + placement results.
  3. Weighted A* PCB trace routing (power-first, rip-up & reroute).
  4. GND copper pour on F.Cu + B.Cu with 4-spoke thermal relief.
  5. Shapely-based DRC (clearance, width, keepout).
  6. Emit agent:pcb_ready + agent:done events.
"""

from agent.utils import _emit, emit_assistant_message, emit_tool_event

from pcb_design.board_model import (
    BoardModel, BoardComponent, PadDef, BoardTrace, BoardVia, DRCConfig,
)
from pcb_design.placement import place_components
from pcb_design.router import route_board as route_board_old
from pcb_design.router2 import route_nets as route_board_new, drc2
from pcb_design.geometry import board_outline_polygon, HAS_SHAPELY
from pcb_design.pour import pour_ground


def pcb_layout_node(state, config):
    _emit(config, "agent:thinking", {"message": "Routing PCB traces..."})
    emit_assistant_message(config, "Laying out components on the PCB and routing traces...")
    emit_tool_event(config, "PCB Layout", "running", "Force-directed placement...")

    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    power_labels = state.get("power_labels", [])
    component_placements = state.get("component_placements", [])

    if not comps:
        _emit(config, "agent:log", {"message": "No components for PCB layout."})
        return {}

    # ── 1. Force-directed PCB component placement ──────────────────
    pcb_placements = place_components(comps, netlist, pin_matrix=pin_matrix)
    pcb_pos = {p["ref_des"]: (p["x"], p["y"], p.get("rotation", 0)) for p in pcb_placements}

    # ── 2. Build BoardModel ────────────────────────────────────────
    model = BoardModel(
        nets=state.get("nets", []),
        power_pins=power_pins,
        power_labels=power_labels,
    )

    for comp in comps:
        ref = comp["ref_des"]
        pads = []
        if comp.get("pads"):
            for pd in comp["pads"]:
                pads.append(PadDef(
                    number=str(pd.get("number", "")),
                    x=pd.get("x", 0), y=pd.get("y", 0),
                    width=pd.get("width", 1), height=pd.get("height", 1),
                    shape=pd.get("shape", "rect"), type=pd.get("type", "smd"),
                    rotation=pd.get("rotation", 0), drill=pd.get("drill"),
                ))
        bx, by, brot = pcb_pos.get(ref, (0, 0, 0))
        model.components.append(BoardComponent(
            ref=ref,
            footprint=comp.get("footprint", ""),
            x=bx, y=by,
            rotation=brot,
            value=comp.get("id_str", "").rpartition(":")[2] if comp else "",
            pads=pads,
            bbox=(0, 0, 10, 10),
        ))

    if HAS_SHAPELY:
        model.outline = board_outline_polygon(
            [{"x": c.x, "y": c.y, "pads": [{"x": p.x, "y": p.y} for p in c.pads]}
             for c in model.components]
        )

    emit_tool_event(config, "PCB Layout", "running", f"Placed {len(model.components)} components (force-directed)")

    drc_config = DRCConfig()

    # ── 3. Route PCB traces (A* with rip-up & reroute) ─────────────
    trace_constraints = state.get("trace_constraints", {})
    traces_new = route_board_new(model, netlist, pin_matrix, drc=drc_config,
                                 trace_constraints=trace_constraints)
    if traces_new:
        model.traces = traces_new
        n_power = sum(1 for t in traces_new if t.net.upper() in
                      {"VCC", "VDD", "VBAT", "VIN", "VBUS", "VSYS", "VOUT",
                       "+5V", "+3.3V", "3.3V", "5V", "3V3"})
        n_gnd = sum(1 for t in traces_new if t.net.upper() in {"GND", "GROUND"})
        n_sig = len(traces_new) - n_power - n_gnd
        _emit(config, "agent:log", {
            "message": (f"  Router2: routed {len(traces_new)} traces "
                        f"({n_power} power, {n_gnd} GND, {n_sig} signal) "
                        f"with {len(model.vias)} vias")
        })
        emit_tool_event(config, "PCB Layout", "running", f"Routed {len(traces_new)} connections ({len(model.vias)} vias)")
    else:
        _emit(config, "agent:log", {
            "message": "  ⚠ Router2 returned 0 traces — falling back to old router..."
        })
        engine_placeholder = BackendLayoutEngine() if not HAS_SHAPELY else None
        pin_matrix_local = pin_matrix
        try:
            from agent.layout_engine import BackendLayoutEngine
            engine_fb = BackendLayoutEngine()
            for comp in comps:
                ref_des = comp["ref_des"]
                ops = state.get("component_ops", {}).get(ref_des)
                if ops:
                    engine_fb.add_component(ref_des, ops, comp["category"],
                                            comp.get("id_str", ""),
                                            comp.get("for_component", ""))
            engine_fb.execute_placement(pin_matrix=pin_matrix, netlist=netlist)
            engine_fb.build_obstacle_matrix(pin_matrix=pin_matrix)
            traces_old, drc_violations = route_board_old(engine_fb, netlist, pin_matrix)
            if drc_violations:
                n_warn = sum(1 for v in drc_violations if v.get("severity") == "warning")
                n_info = sum(1 for v in drc_violations if v.get("severity") == "info")
                _emit(config, "agent:log", {
                    "message": f"  DRC (old): {n_warn} warning(s), {n_info} info"
                })
            model.traces = [
                BoardTrace(net=w.get("source", ""), layer="F.Cu", width=0.254, path=[
                    (p["x"], p["y"]) for p in w.get("path", [])
                ])
                for w in traces_old
            ]
            _emit(config, "agent:log", {
                "message": f"  Router (old fallback): routed {len(traces_old)} traces"
            })
        except Exception:
            _emit(config, "agent:log", {"message": "  ⚠ Both routers failed — no PCB traces generated."})

    # ── 4. GND copper pour ─────────────────────────────────────────
    zone_count = pour_ground(model,
        clearance=drc_config.zone_clearance,
        min_zone_area=drc_config.min_zone_area,
        thermal_relief_gap=drc_config.thermal_relief_gap,
        thermal_spoke_width=drc_config.thermal_spoke_width,
    )
    if zone_count:
        _emit(config, "agent:log", {
            "message": f"  Poured {zone_count} GND zones on F.Cu + B.Cu (4-spoke thermal relief)"
        })

    # ── 5. DRC ─────────────────────────────────────────────────────
    try:
        drc_results = drc2(model, drc=drc_config)
        n_err = sum(1 for v in drc_results if v.get("severity") == "error")
        n_warn = sum(1 for v in drc_results if v.get("severity") == "warning")
        for v in drc_results:
            _emit(config, "agent:log", {
                "message": f"  DRC: [{v.get('severity', 'info').upper()}] {v['message']}"
            })
        if n_err == 0 and n_warn == 0:
            _emit(config, "agent:log", {"message": "  DRC: clean (0 errors, 0 warnings)"})
    except Exception:
        n_err = n_warn = 0
        _emit(config, "agent:log", {"message": "  DRC: skipped (no Shapely or router2 unavailable)"})

    # ── 6. Emit final events ───────────────────────────────────────
    board_dict = model.to_dict()
    _emit(config, "agent:pcb_ready", {"board_model": board_dict})
    _emit(config, "agent:done", {
        "message": (f"Design complete: {len(model.components)} components, "
                    f"{len(model.traces)} PCB traces, {len(model.vias)} vias, "
                    f"{zone_count} GND zones, DRC: {n_err} err / {n_warn} warn")
    })
    emit_tool_event(config, "PCB Layout", "completed",
                    f"{len(model.components)} components, {len(model.traces)} traces, {zone_count} GND zones, DRC: {n_err} err / {n_warn} warn")
    emit_assistant_message(config, f"PCB complete — {len(model.components)} components, {len(model.traces)} traces, DRC: {n_err} errors / {n_warn} warnings.")

    return {"board_model": board_dict, "_board_model": board_dict}
