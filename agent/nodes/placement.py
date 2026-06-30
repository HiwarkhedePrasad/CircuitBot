"""Placement node — runs component placement ONCE and locks."""

from agent.layout_engine import BackendLayoutEngine
from agent.utils import _emit, emit_assistant_message, emit_tool_event


def placement_node(state, config):
    if state.get("_placement_locked"):
        raise RuntimeError("Placement already computed — re-entry would move components.")

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])

    if not comps or not comp_ops:
        emit_tool_event(config, "Placement", "failed", "No components to place.")
        return {}

    _emit(config, "agent:thinking", {"message": "Placing components on the schematic sheet..."})
    emit_assistant_message(config, "Running schematic placement...")
    emit_tool_event(config, "Placement", "running", "Placing components...")

    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(ref_des, ops, comp["category"],
                             comp.get("id_str", ""),
                             comp.get("for_component", ""))

    if not engine.components:
        emit_tool_event(config, "Placement", "failed", "No components could be placed.")
        return {}

    # Run placement with built-in 5-retry loop (best-score selected)
    engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

    placements = engine.get_placements()

    emit_tool_event(config, "Placement", "completed",
                    f"Placed {len(placements)} components")
    emit_assistant_message(config, f"Placement complete — {len(placements)} components placed.")

    return {
        "component_placements": placements,
        "_placement_locked": True,
    }
