from agent.deep_search import deep_search
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)


def datasheet_search_node(state, config):
    _emit(config, "agent:thinking", {"message": "Researching datasheets for selected components..."})
    emit_assistant_message(config, "Searching for datasheets and specifications of selected components...")
    emit_tool_event(config, "Datasheet Research", "running", "Searching datasheets...")

    contract = _check_stage_contract("datasheet_search", state, ["selected_components"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "datasheet_search", {"datasheet_search_results": []})

    selected = state.get("selected_components", [])
    if not selected:
        _emit(config, "agent:log", {"message": "No components to research."})
        return _stage_result(state, "datasheet_search", {"datasheet_search_results": []})

    results = []
    for comp in selected:
        ref = comp.get("ref_des", "?")
        id_str = comp.get("id_str", "")
        desc = comp.get("description", "") or id_str
        _emit(config, "agent:thinking", {"message": f"Searching datasheet for {ref} ({id_str})..."})
        emit_tool_event(config, f"Datasheet: {ref}", "running", f"Searching {id_str}...")
        try:
            summary = deep_search(
                f"Find the datasheet and key specifications for electronic component {id_str}. "
                f"Description: {desc}. "
                f"Return: datasheet URL, manufacturer, key parameters (voltage, current, package), "
                f"and a brief summary of the part.",
                config=config,
            )
            results.append({
                "ref_des": ref,
                "id_str": id_str,
                "summary": summary,
            })
            _emit(config, "agent:log", {"message": f"  {ref} ({id_str}): datasheet research complete"})
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  {ref} ({id_str}): datasheet search failed — {e}"})
            results.append({
                "ref_des": ref,
                "id_str": id_str,
                "summary": f"(Datasheet search failed: {e})",
            })
        emit_tool_event(config, f"Datasheet: {ref}", "completed",
                        f"Found datasheet info for {id_str}" if results[-1].get("summary") and not results[-1]["summary"].startswith("(") else f"Search failed for {id_str}")

    emit_tool_event(config, "Datasheet Research", "completed",
                    f"Researched {len(results)} components")
    emit_assistant_message(config, f"Found datasheet information for {len(results)} components.")
    return _stage_result(state, "datasheet_search", {"datasheet_search_results": results})
