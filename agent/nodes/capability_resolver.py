"""Capability Resolver — maps board type capabilities to builtin components.

After the architecture planner determines board_type, this node adds
"builtin" component entries for capabilities that are already provided
by the board. These components are marked as locked and cannot be
removed or replaced by downstream stages.
"""

from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)
from uuid import uuid4


# Mapping from capability name to component info for builtin injection
_CAPABILITY_COMPONENTS: dict[str, dict] = {
    "usb_to_serial": {
        "id_str": "Interface_USB:CP2102N",
        "category": "Interface_USB",
        "description": "USB-to-UART bridge (builtin on devkit)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by devkit board",
    },
    "regulator_3v3": {
        "id_str": "Regulator_Linear:AMS1117-3.3",
        "category": "Regulator_Linear",
        "description": "3.3V LDO regulator (builtin on devkit)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by devkit board",
    },
    "reset_button": {
        "id_str": "Switch:SW_Push",
        "category": "Switch",
        "description": "Reset button (builtin on devkit)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by devkit board",
    },
    "boot_button": {
        "id_str": "Switch:SW_Push",
        "category": "Switch",
        "description": "Boot/programming button (builtin on devkit)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by devkit board",
    },
    "status_led": {
        "id_str": "Device:LED",
        "category": "LED",
        "description": "Status LED (builtin on devkit)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by devkit board",
    },
    "antenna": {
        "id_str": "RF_Module:Antenna",
        "category": "RF_Module",
        "description": "Integrated antenna (builtin on module)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by module",
    },
    "crystal": {
        "id_str": "Device:Crystal",
        "category": "Crystal",
        "description": "Crystal oscillator (builtin on module)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by module",
    },
    "flash": {
        "id_str": "Memory_Flash:W25Q",
        "category": "Memory_Flash",
        "description": "QSPI flash memory (builtin on module)",
        "footprint": "",
        "pads": [],
        "justification": "Built-in: provided by module",
    },
}


def capability_resolver_node(state, config):
    """Add builtin components for capabilities provided by the board type."""
    cap_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Resolving board capabilities..."})
    emit_assistant_message(config, "Mapping board capabilities to builtin components...")
    emit_tool_event(config, "Capability Resolver", "running", "Resolving capabilities...")
    
    contract = _check_stage_contract("capability_resolver", state, ["board_type", "provides"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "capability_resolver", {})
    
    board_type = state["board_type"]
    provides = state.get("provides", {})
    
    # Collect builtin components for capabilities that are provided
    builtin_components = []
    for capability, is_provided in provides.items():
        if not is_provided:
            continue
        cap_info = _CAPABILITY_COMPONENTS.get(capability)
        if not cap_info:
            continue
        
        # Create a builtin component entry
        builtin = dict(cap_info)
        builtin["subsystem"] = f"builtin_{capability}"
        builtin["user_locked"] = True  # Cannot be replaced
        builtin["builtin"] = True      # Mark as builtin
        builtin_components.append(builtin)
    
    if builtin_components:
        _emit(config, "agent:log", {
            "message": f"  Added {len(builtin_components)} builtin component(s): "
                       f"{', '.join(c['description'].split('(')[0].strip() for c in builtin_components)}"
        })
    else:
        _emit(config, "agent:log", {"message": "  No builtin components to add"})
    
    emit_tool_event(config, "Capability Resolver", "completed",
                    f"{len(builtin_components)} builtin components resolved")
    
    return _stage_result(state, "capability_resolver", {
        "_builtin_components": builtin_components,
    })
