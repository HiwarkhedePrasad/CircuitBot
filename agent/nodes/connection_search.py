import json

from agent.deep_search import deep_search
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _clean_json,
)


def _group_components_by_interface(selected: list[dict], analysis: list[dict]) -> list[dict]:
    """Build a list of connection research tasks from selected components.
    Groups by bus type (I2C, SPI, UART, USB, etc.) to avoid redundant searches."""
    bus_map = {}
    for sub in (analysis or []):
        sub_name = sub.get("subsystem", "")
        bus = sub.get("bus", "any")
        for comp in selected:
            if comp.get("subsystem") == sub_name:
                bus_map.setdefault(bus, []).append(comp)

    tasks = []
    for bus, comps in bus_map.items():
        comps_str = ", ".join(f"{c.get('ref_des','?')} ({c.get('id_str','?')})" for c in comps)
        if bus and bus != "any":
            tasks.append({
                "title": f"{bus} bus connections ({comps_str})",
                "query": (
                    f"How to connect components over {bus} bus: {comps_str}. "
                    f"Return: pull-up resistor values, recommended pin assignments, "
                    f"and typical wiring diagram description."
                ),
            })

    # Also add MCU→peripheral connections for non-bus interfaces
    mcu_comp = None
    peripherals = []
    for comp in selected:
        cid = comp.get("id_str", "").upper()
        if any(kw in cid for kw in ("MCU", "ESP32", "STM32", "RP2040", "ATMEGA", "ATTINY", "PROCESSOR")):
            mcu_comp = comp
        else:
            peripherals.append(comp)

    if mcu_comp and peripherals:
        mcu_name = f"{mcu_comp.get('ref_des','?')} ({mcu_comp.get('id_str','?')})"
        for peri in peripherals:
            peri_name = f"{peri.get('ref_des','?')} ({peri.get('id_str','?')})"
            tasks.append({
                "title": f"{mcu_comp.get('ref_des','?')} → {peri.get('ref_des','?')}",
                "query": (
                    f"How to connect {mcu_name} to {peri_name}. "
                    f"Return: required pins, interface type, "
                    f"typical circuit diagram description, and any external components needed."
                ),
            })

    return tasks


def connection_search_node(state, config):
    _emit(config, "agent:thinking", {"message": "Researching wiring and connections..."})
    emit_assistant_message(config, "Searching for connection guidance and wiring diagrams...")
    emit_tool_event(config, "Connection Research", "running", "Researching connections...")

    contract = _check_stage_contract("connection_search", state, ["selected_components"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "connection_search", {"connection_search_results": []})

    selected = state.get("selected_components", [])
    analysis = state.get("analysis", [])
    if not selected:
        _emit(config, "agent:log", {"message": "No components to research connections for."})
        return _stage_result(state, "connection_search", {"connection_search_results": []})

    tasks = _group_components_by_interface(selected, analysis)
    if not tasks:
        tasks.append({
            "title": "General connections",
            "query": (
                f"Describe typical wiring connections between these components: "
                + ", ".join(f"{c.get('ref_des','?')} ({c.get('id_str','?')})" for c in selected)
            ),
        })

    results = []
    for task in tasks:
        _emit(config, "agent:thinking", {"message": f"Researching {task['title']}..."})
        emit_tool_event(config, f"Connection: {task['title']}", "running", f"Searching...")
        try:
            summary = deep_search(task["query"], config=config)
            results.append({
                "title": task["title"],
                "summary": summary,
            })
            _emit(config, "agent:log", {"message": f"  {task['title']}: connection research complete"})
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  {task['title']}: connection search failed — {e}"})
            results.append({
                "title": task["title"],
                "summary": f"(Connection search failed: {e})",
            })
        emit_tool_event(config, f"Connection: {task['title']}", "completed",
                        "Research complete" if not task["title"].startswith("(") else "Failed")

    emit_tool_event(config, "Connection Research", "completed",
                    f"Researched {len(results)} connection(s)")
    emit_assistant_message(config, f"Found connection guidance for {len(results)} interface(s).")
    return _stage_result(state, "connection_search", {"connection_search_results": results})
