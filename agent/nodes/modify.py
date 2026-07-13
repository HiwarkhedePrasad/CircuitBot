"""Modification pipeline nodes for conversational design refinement."""

import json
from agent.llm_utils import _call_llm
from agent.emit_utils import _clean_json


MODIFY_CLASSIFY_SYSTEM = """You are a circuit design modification classifier.
Given a user's modification request, classify it and extract the target and value.

Return JSON:
{
  "modification_type": "value_change" | "part_swap" | "add_component" | "remove_component" | "net_modify" | "reroute",
  "target": {"ref": "R1"} or {"net": "VCC"} or {"description": "..."},
  "value": {"value": "10k"} or {"part_id": "..."} or {"description": "..."}
}

Examples:
- "Change R1 to 10k" -> {"modification_type": "value_change", "target": {"ref": "R1"}, "value": {"value": "10k"}}
- "Swap U1 for MCP1700" -> {"modification_type": "part_swap", "target": {"ref": "U1"}, "value": {"part_id": "MCP1700"}}
- "Add a 100nF cap on VCC" -> {"modification_type": "add_component", "target": {"net": "VCC"}, "value": {"description": "100nF bypass capacitor"}}
- "Remove R3" -> {"modification_type": "remove_component", "target": {"ref": "R3"}, "value": {}}
- "Connect LED to pin 13" -> {"modification_type": "net_modify", "target": {"description": "LED"}, "value": {"pin": "13"}}
- "Make power traces wider" -> {"modification_type": "reroute", "target": {"net": "VCC"}, "value": {"trace_width": "0.5mm"}}

Return ONLY the JSON object."""


def classify_modification_node(state: dict) -> dict:
    """Classify the modification request using LLM."""
    prompt = state.get("prompt", "")
    original = state.get("original_design", {})

    # Build context about existing components
    components = original.get("selected_components", [])
    comp_list = ", ".join(
        f"{c.get('ref', '?')} ({c.get('name', '?')})" for c in components[:20]
    )

    user_msg = f"Existing components: [{comp_list}]\n\nUser request: {prompt}"

    try:
        raw = _call_llm(MODIFY_CLASSIFY_SYSTEM, user_msg, stage="modify_classify")
        raw = _clean_json(raw)
        result = json.loads(raw) if raw else {}
    except Exception:
        result = {}

    return {
        "modification_type": result.get("modification_type", "unknown"),
        "modification_target": result.get("target", {}),
        "modification_value": result.get("value", {}),
    }


def apply_modification_node(state: dict) -> dict:
    """Apply the classified modification to the design."""
    mod_type = state.get("modification_type", "unknown")
    target = state.get("modification_target", {})
    value = state.get("modification_value", {})
    original = state.get("original_design", {})

    components = list(original.get("selected_components", []))
    board_model = dict(original.get("board_model", {}) or {})
    nets = list(original.get("nets", []) or [])

    if mod_type == "value_change":
        ref = target.get("ref", "")
        new_val = value.get("value", "")
        for comp in components:
            if comp.get("ref") == ref or comp.get("ref_des") == ref:
                comp["value"] = new_val
                break
        # Also update board_model components
        for comp in board_model.get("components", []):
            if comp.get("ref") == ref:
                comp["value"] = new_val
                break

    elif mod_type == "remove_component":
        ref = target.get("ref", "")
        components = [c for c in components if c.get("ref") != ref and c.get("ref_des") != ref]
        board_model["components"] = [
            c for c in board_model.get("components", []) if c.get("ref") != ref
        ]
        # Remove from nets
        for net in nets:
            pins = net.get("pins", [])
            net["pins"] = [p for p in pins if not p.startswith(ref + ":")]

    elif mod_type == "add_component":
        desc = value.get("description", "component")
        new_ref = f"X{len(components) + 1}"
        new_comp = {
            "ref": new_ref,
            "ref_des": new_ref,
            "name": desc,
            "value": desc,
            "footprint": "",
            "category": "added",
            "description": desc,
            "pads": [],
            "justification": "User requested addition",
            "datasheet_text": "",
        }
        components.append(new_comp)

    elif mod_type == "part_swap":
        ref = target.get("ref", "")
        new_part = value.get("part_id", "")
        for comp in components:
            if comp.get("ref") == ref or comp.get("ref_des") == ref:
                comp["value"] = new_part
                comp["name"] = new_part
                break

    elif mod_type == "net_modify":
        # Update net connections
        pass  # Will be refined in future iterations

    elif mod_type == "reroute":
        # Update trace constraints
        pass  # Will be refined in future iterations

    return {
        "selected_components": components,
        "board_model": board_model,
        "nets": nets,
        "_stage": "modify_complete",
    }
