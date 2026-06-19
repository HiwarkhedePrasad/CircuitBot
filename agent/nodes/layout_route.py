from agent.layout_engine import BackendLayoutEngine
from agent.utils import _emit, _emit_activity

from pcb_design.board_model import (
    BoardModel, BoardComponent, PadDef, BoardTrace, BoardVia, DRCConfig,
)
from pcb_design.placement import place_components
from pcb_design.router import route_board as route_board_old
from pcb_design.router2 import route_nets as route_board_new, drc2
from pcb_design.geometry import board_outline_polygon, HAS_SHAPELY
from pcb_design.pour import pour_ground


def layout_route_node(state, config):
    _emit(config, "agent:thinking", {"message": "Computing layout and routing wires..."})
    _emit_activity(config, "layout", "PCB Layout", "start")
    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    if not comps or not comp_ops:
        _emit(config, "agent:done", {"message": "No components to place."})
        return {}

    # ── 1. Build BoardModel ────────────────────────────────────────────
    model = BoardModel(
        nets=state.get("nets", []),
        power_pins=power_pins,
        power_labels=state.get("power_labels", []),
    )

    # Import component ops to get pad data
    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(ref_des, ops, comp["category"])
    if not engine.components:
        _emit(config, "agent:done", {"message": "No components could be placed."})
        return {}

    # ── 2. Place components ────────────────────────────────────────────
    pcb_placements = place_components(comps, netlist)
    if pcb_placements:
        for p in pcb_placements:
            engine.set_component_position(
                p["ref_des"], p["x"], p["y"],
                rotation=p.get("rotation", 0),
            )
    else:
        engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

    # Convert engine components to BoardComponent with pads
    for ec in engine.components:
        ref = ec["ref_des"]
        # Find matching component with pad data
        comp_info = next((c for c in comps if c["ref_des"] == ref), None)
        pads = []
        if comp_info and comp_info.get("pads"):
            for pd in comp_info["pads"]:
                pads.append(PadDef(
                    number=str(pd.get("number", "")),
                    x=pd.get("x", 0), y=pd.get("y", 0),
                    width=pd.get("width", 1), height=pd.get("height", 1),
                    shape=pd.get("shape", "rect"), type=pd.get("type", "smd"),
                    rotation=pd.get("rotation", 0), drill=pd.get("drill"),
                ))
        bbox = ec.get("bbox", {})
        model.components.append(BoardComponent(
            ref=ref,
            footprint=comp_info.get("footprint", "") if comp_info else "",
            x=ec["x"], y=ec["y"],
            rotation=ec.get("rotation", 0),
            value=comp_info.get("id_str", "").rpartition(":")[2] if comp_info else "",
            pads=pads,
            bbox=(bbox.get("x", 0), bbox.get("y", 0), bbox.get("w", 10), bbox.get("h", 10)),
        ))

    if HAS_SHAPELY:
        model.outline = board_outline_polygon(
            [{"x": c.x, "y": c.y, "pads": [{"x": p.x, "y": p.y} for p in c.pads]}
             for c in model.components]
        )

    _emit_activity(config, "layout", "PCB Layout", "update", kind="placement",
                   detail=f"Placed {len(model.components)} components")

    drc_config = DRCConfig()

    # ── 3. Route ───────────────────────────────────────────────────────
    traces_new = route_board_new(model, netlist, pin_matrix, drc=drc_config)
    if traces_new:
        model.traces = traces_new
        _emit(config, "agent:log", {
            "message": f"  Router2: routed {len(traces_new)} traces"
        })
        _emit_activity(config, "layout", "PCB Layout", "update", kind="routing", detail=f"Routed {len(traces_new)} connections")
    else:
        # Fallback to old router
        engine.build_obstacle_matrix(pin_matrix=pin_matrix)
        traces_old, drc_violations = route_board_old(engine, netlist, pin_matrix)
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
            "message": f"  Router (old): routed {len(traces_old)} traces"
        })
        _emit_activity(config, "layout", "PCB Layout", "update", kind="routing", detail=f"Routed {len(traces_old)} connections")

    # ── 4. Zone pouring ────────────────────────────────────────────────
    zone_count = pour_ground(model,
        clearance=drc_config.zone_clearance,
        min_zone_area=drc_config.min_zone_area,
        thermal_relief_gap=drc_config.thermal_relief_gap,
        thermal_spoke_width=drc_config.thermal_spoke_width,
    )
    if zone_count:
        _emit(config, "agent:log", {
            "message": f"  Poured {zone_count} GND zones on F.Cu + B.Cu"
        })

    # ── 5. DRC ─────────────────────────────────────────────────────────
    drc_results = drc2(model, drc=drc_config)
    for v in drc_results:
        _emit(config, "agent:log", {
            "message": f"  DRC: [{v.get('severity', 'info').upper()}] {v['message']}"
        })

    placements = engine.get_placements()

    # ── 6. Power labels ────────────────────────────────────────────────
    power_labels = []
    for pp in power_pins:
        pin_obj = pin_matrix.get(pp["pin"])
        if not pin_obj:
            continue
        ref = pp["pin"].split(":")[0]
        comp = engine._get_comp(ref)
        if not comp:
            continue
        ax = pin_obj["x"] + comp["x"]
        ay = pin_obj["y"] + comp["y"]
        ccx = comp["x"] + comp["bbox"]["x"] + comp["bbox"]["w"] / 2
        ccy = comp["y"] + comp["bbox"]["y"] + comp["bbox"]["h"] / 2
        dx = ax - ccx
        dy = ay - ccy
        direction = "right" if abs(dx) >= abs(dy) else ("up" if dy >= 0 else "down")
        if abs(dx) < abs(dy):
            direction = "up" if dy >= 0 else "down"
        else:
            direction = "right" if dx >= 0 else "left"
        power_labels.append({
            "pin": pp["pin"],
            "net": pp["net"],
            "x": ax,
            "y": ay,
            "dir": direction,
        })

    # ── 7. Emit ────────────────────────────────────────────────────────
    board_dict = model.to_dict()
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": [
            {"source": t.net, "path": [{"x": p[0], "y": p[1]} for p in t.path]}
            for t in model.traces
        ],
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })
    _emit(config, "agent:pcb_ready", {"board_model": board_dict})
    _emit(config, "agent:done", {
        "message": f"Design complete: {len(model.components)} components, "
                   f"{len(model.traces)} signal wires, {len(power_labels)} power symbols"
    })
    _emit_activity(config, "layout", "PCB Layout", "done")
    return {
        "component_placements": placements,
        "wire_paths": [
            {"source": t.net, "path": [{"x": p[0], "y": p[1]} for p in t.path]}
            for t in model.traces
        ],
        "power_labels": power_labels,
        "board_model": board_dict,
        "_board_model": board_dict,
    }
