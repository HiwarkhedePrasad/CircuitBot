from uuid import uuid4

from agent.deep_search import deep_search_parallel
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)


def datasheet_search_node(state, config):
    _emit(config, "agent:thinking", {"message": "Researching datasheets for selected components..."})
    emit_assistant_message(config, "Searching for datasheets and specifications of selected components...")
    tool_id = uuid4().hex[:8]
    emit_tool_event(config, "Datasheet Research", "running", "Searching datasheets...",
                    tool_id=tool_id)

    contract = _check_stage_contract("datasheet_search", state, ["selected_components"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        emit_tool_event(config, "Datasheet Research", "completed",
                        "No components to research", tool_id=tool_id)
        return _stage_result(state, "datasheet_search", {"datasheet_search_results": []})

    selected = state.get("selected_components", [])
    if not selected:
        _emit(config, "agent:log", {"message": "No components to research."})
        emit_tool_event(config, "Datasheet Research", "completed",
                        "No components to research", tool_id=tool_id)
        return _stage_result(state, "datasheet_search", {"datasheet_search_results": []})

    # Build queries for parallel execution
    queries = []
    meta = []
    for comp in selected:
        ref = comp.get("ref_des", "?")
        id_str = comp.get("id_str", "")
        desc = comp.get("description", "") or id_str
        queries.append(
            f"Find the datasheet and key specifications for electronic component {id_str}. "
            f"Description: {desc}. "
            f"Return: datasheet URL, manufacturer, key parameters (voltage, current, package), "
            f"and a brief summary of the part."
        )
        meta.append({"ref_des": ref, "id_str": id_str})

    _emit(config, "agent:thinking", {"message": f"Searching datasheets for {len(selected)} components (2 concurrent)..."})
    search_results = deep_search_parallel(queries, config=config)

    results = []
    for i, result in enumerate(search_results):
        m = meta[i]
        results.append({
            "ref_des": m["ref_des"],
            "id_str": m["id_str"],
            "summary": result["summary"],
        })
        if result["success"]:
            _emit(config, "agent:log", {"message": f"  {m['ref_des']} ({m['id_str']}): datasheet research complete"})
        else:
            _emit(config, "agent:log", {"message": f"  {m['ref_des']} ({m['id_str']}): datasheet search failed"})

    emit_tool_event(config, "Datasheet Research", "completed",
                    f"Researched {len(results)} components", tool_id=tool_id)
    emit_assistant_message(config, f"Found datasheet information for {len(results)} components.")
    return _stage_result(state, "datasheet_search", {"datasheet_search_results": results})
