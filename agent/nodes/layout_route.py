from agent.layout_engine import BackendLayoutEngine
from agent.utils import _emit


def layout_route_node(state, config):
    _emit(config, "agent:thinking", {"message": "Computing layout and routing wires..."})
    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    if not comps or not comp_ops:
        _emit(config, "agent:done", {"message": "No components to place."})
        return {}
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
    from pcb_design.placement import place_components
    pcb_placements = place_components(comps, netlist)
    if pcb_placements:
        for p in pcb_placements:
            engine.set_component_position(
                p["ref_des"], p["x"], p["y"],
                rotation=p.get("rotation", 0),
            )
    else:
        engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)
    engine.build_obstacle_matrix(pin_matrix=pin_matrix)
    from pcb_design.router import route_board
    traces, drc_violations = route_board(engine, netlist, pin_matrix)
    if drc_violations:
        n_warn = sum(1 for v in drc_violations if v.get("severity") == "warning")
        n_info = sum(1 for v in drc_violations if v.get("severity") == "info")
        _emit(config, "agent:log", {
            "message": f"  DRC: {n_warn} warning(s), {n_info} info"
        })
    placements = engine.get_placements()
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
        ccx = comp["x"] + comp["geom_bbox"]["x"] + comp["geom_bbox"]["w"] / 2
        ccy = comp["y"] + comp["geom_bbox"]["y"] + comp["geom_bbox"]["h"] / 2
        dx = ax - ccx
        dy = ay - ccy
        if abs(dx) >= abs(dy):
            direction = "right" if dx >= 0 else "left"
        else:
            direction = "up" if dy >= 0 else "down"
        power_labels.append({
            "pin": pp["pin"],
            "net": pp["net"],
            "x": ax,
            "y": ay,
            "dir": direction,
        })
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })
    _emit(config, "agent:done", {
        "message": f"Design complete: {len(engine.components)} components, "
                   f"{len(traces)} signal wires, {len(power_labels)} power symbols"
    })
    return {
        "component_placements": placements,
        "wire_paths": traces,
        "power_labels": power_labels,
    }
