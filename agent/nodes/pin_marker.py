"""Pin Marker — marks NC/Reserved/Test/Internal pins as intentionally unused.

This runs BEFORE netlist generation so the bus checker excludes these
pins from "pins not covered" counts. It uses deterministic patterns
per MCU family and general rules.
"""

import re

from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)
from uuid import uuid4


# ── Known unused pin patterns ──────────────────────────────────────────

# General patterns that always mean "unused"
_GENERAL_UNUSED_PATTERNS = re.compile(
    r'^(NC|NO[-_]?CONNECT|N\.?C\.?|RESERVED|RES|INTERNAL|TEST|'
    r'DNC|DO[-_]?NOT[-_]?CONNECT|NOT[-_]?CONNECTED|'
    r'FAB|FABRICATION|TBD|UNCONNECTED|UNUSED)$',
    re.IGNORECASE,
)

# ESP32-specific NC/reserved pin patterns
_ESP32_UNUSED_LIBS = frozenset({"MCU_ESPRESSIF", "RF_MODULE"})
_ESP32_UNUSED_PINS = re.compile(
    r'^(NC\d*|RESERVED\d*|DNC|DO[-_]?NOT[-_]?CONNECT)$',
    re.IGNORECASE,
)

# RP2040-specific patterns
_RP2040_UNUSED_PINS = re.compile(
    r'^(NC\d*|RESERVED\d*|TEST\d*|DNC|DO[-_]?NOT[-_]?CONNECT)$',
    re.IGNORECASE,
)

# STM32-specific patterns
_STM32_UNUSED_PINS = re.compile(
    r'^(NC\d*|RESERVED\d*|DNC|DO[-_]?NOT[-_]?CONNECT)$',
    re.IGNORECASE,
)

# ATmega-specific patterns
_ATMEGA_UNUSED_PINS = re.compile(
    r'^(NC\d*|RESERVED\d*|DNC|DO[-_]?NOT[-_]?CONNECT)$',
    re.IGNORECASE,
)

# USB-C connector NC pins
_USBC_UNUSED_PINS = re.compile(
    r'^(NC\d*|RESERVED\d*|DNC|DO[-_]?NOT[-_]?CONNECT)$',
    re.IGNORECASE,
)


def _should_mark_unused(pin_name: str, comp_id_str: str) -> bool:
    """Determine if a pin should be marked as intentionally unused."""
    canonical = pin_name.strip().upper().replace("_", "").replace("-", "").replace(" ", "")
    if not canonical:
        return False

    # General NC/Reserved patterns
    if _GENERAL_UNUSED_PATTERNS.match(canonical):
        return True

    # MCU-family-specific patterns
    comp_upper = comp_id_str.upper()

    if "ESP32" in comp_upper:
        if _ESP32_UNUSED_PINS.match(canonical):
            return True

    if "RP2040" in comp_upper or "RP2350" in comp_upper:
        if _RP2040_UNUSED_PINS.match(canonical):
            return True

    if "STM32" in comp_upper:
        if _STM32_UNUSED_PINS.match(canonical):
            return True

    if "ATMEGA" in comp_upper or "ATTINY" in comp_upper:
        if _ATMEGA_UNUSED_PINS.match(canonical):
            return True

    # USB-C connector NC pins
    if "USB_C" in comp_upper or "USB-C" in comp_upper:
        if _USBC_UNUSED_PINS.match(canonical):
            return True

    return False


def pin_marker_node(state, config):
    """Mark NC/Reserved/Test/Internal pins as intentionally unused."""
    mark_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Marking unused pins..."})
    emit_assistant_message(config, "Identifying NC, reserved, and test pins...")
    emit_tool_event(config, "Pin Marker", "running", "Marking unused pins...")

    contract = _check_stage_contract("pin_marker", state, ["pin_matrix"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "pin_marker", {})

    pin_matrix = dict(state.get("pin_matrix", {}))
    components = state.get("selected_components", [])

    # Build ref_des → id_str lookup
    ref_to_id = {c["ref_des"]: c.get("id_str", "") for c in components}

    marked_count = 0
    for pin_key, pin_info in pin_matrix.items():
        ref = pin_key.split(":")[0] if ":" in pin_key else ""
        pin_name = pin_info.get("name", "")
        comp_id = ref_to_id.get(ref, "")

        if _should_mark_unused(pin_name, comp_id):
            pin_info["unused"] = True
            marked_count += 1

    _emit(config, "agent:log", {
        "message": f"  Marked {marked_count} pin(s) as intentionally unused"
    })

    emit_tool_event(config, "Pin Marker", "completed",
                    f"{marked_count} pins marked as unused")

    return _stage_result(state, "pin_marker", {
        "pin_matrix": pin_matrix,
    })
