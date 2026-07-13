"""Placement node — runs component placement ONCE and locks."""

from agent.placement import PlacementEngine
from agent.utils import _emit, emit_assistant_message, emit_tool_event, emit_thought, emit_tool_call, emit_tool_end
from uuid import uuid4


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

    place_id = uuid4().hex[:8]
    emit_tool_call(config, place_id, "Schematic Placement", "running")
    emit_thought(config, "Placing components on the schematic sheet...")
    emit_assistant_message(config, "Running schematic placement...")
    emit_tool_event(config, "Placement", "running", "Placing components...")

    # Build component dicts in the format expected by the placement engines
    components = []
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        components.append({
            "ref_des": ref_des,
            "ops": ops,
            "category": comp["category"],
            "id_str": comp.get("id_str", ""),
            "for_component": comp.get("for_component", ""),
        })

    if not components:
        emit_tool_event(config, "Placement", "failed", "No components could be placed.")
        return {}

    engine = PlacementEngine.create()
    placements = engine.place(components, netlist, pin_matrix)

    emit_tool_event(config, "Placement", "completed",
                    f"Placed {len(placements)} components")
    emit_tool_end(config, place_id, f"Placed {len(placements)} components")
    emit_assistant_message(config, f"Placement complete — {len(placements)} components placed.")

    return {
        "component_placements": placements,
        "_placement_locked": True,
    }
