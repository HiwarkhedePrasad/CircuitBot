"""Architecture Planner — determines board type and freezes MCU selection.

This node runs AFTER analyze and BEFORE component selection. It decides:
  1. What kind of board this is (devkit, module, bare_ic, custom_pcb)
  2. What MCU family is primary
  3. What capabilities are already provided (builtin)

Once this node runs, the architecture is FROZEN. No subsequent node may
change board_type or primary_mcu.
"""

import re

from agent.knowledge.board_types import (
    BOARD_TYPES,
    infer_board_type_from_prompt,
    get_provides,
)
from agent.knowledge.dependency_graph import get_mcu_family
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _extract_part_numbers,
)
from uuid import uuid4


def _extract_mcu_from_analysis(analysis: list[dict], prompt: str) -> str | None:
    """Extract the primary MCU from analysis results or user prompt.
    
    Returns the MCU family string (e.g. "ESP32-C3") or None.
    """
    # First check if user explicitly named a part
    parts = _extract_part_numbers(prompt)
    if parts:
        for part in parts:
            family = get_mcu_family(part)
            if family:
                return family
    
    # Then check analysis for MCU subsystem
    for item in analysis:
        sub = (item.get("subsystem", "") or "").lower()
        if any(kw in sub for kw in ("microcontroller", "mcu", "processor")):
            examples = item.get("example_components", [])
            if examples:
                for ex in examples:
                    family = get_mcu_family(ex)
                    if family:
                        return family
    
    # Fallback: scan prompt for MCU keywords
    prompt_upper = prompt.upper()
    mcu_patterns = [
        (r'\bESP32[-_ ]?(C3|C6|S2|S3|H2)\b', lambda m: f"ESP32-{m.group(1)}"),
        (r'\bESP32\b', lambda m: "ESP32-C3"),
        (r'\bSTM32\w*\b', lambda m: "STM32"),
        (r'\bRP2040\b', lambda m: "RP2040"),
        (r'\bRP2350\b', lambda m: "RP2350"),
        (r'\bATmega32U4\b', lambda m: "ATmega32U4"),
        (r'\bATmega\w*\b', lambda m: "ATmega328"),
    ]
    for pattern, extractor in mcu_patterns:
        m = re.search(pattern, prompt_upper)
        if m:
            return extractor(m)
    
    return None


def _determine_board_type(prompt: str, analysis: list[dict]) -> str:
    """Determine board type from prompt and analysis.
    
    Priority:
    1. Explicit user mention ("dev board", "module", "bare IC")
    2. Implicit from context (prototyping → devkit, minimal → bare_ic)
    3. Default to custom_pcb. A generic "board" request describes the
       deliverable, not a pre-built development board.
    """
    # Check explicit keywords
    inferred = infer_board_type_from_prompt(prompt)
    if inferred:
        return inferred
    
    # Check for prototyping context
    prompt_lower = prompt.lower()
    prototyping_keywords = [
        "prototype", "test", "experiment", "learn", "tutorial",
        "beginner", "simple", "basic", "starter",
    ]
    if any(kw in prompt_lower for kw in prototyping_keywords):
        return "devkit"
    
    # Check for production context
    production_keywords = [
        "production", "manufacture", "deploy", "ship", "sell",
        "minimal", "compact", "small", "cheap",
    ]
    if any(kw in prompt_lower for kw in production_keywords):
        return "bare_ic"
    
    # Default to a custom PCB. Bare IC is an explicit assembly choice and a
    # devkit must be explicitly requested; guessing either silently changes
    # which power, USB, and programming circuitry is required.
    return "custom_pcb"


def architecture_planner_node(state, config):
    """Determine board type and freeze MCU selection."""
    plan_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Planning architecture..."})
    emit_assistant_message(config, "Determining board type and locking MCU selection...")
    emit_tool_event(config, "Architecture Planner", "running", "Analyzing design requirements...")
    
    contract = _check_stage_contract("architecture_planner", state, ["prompt", "analysis"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "architecture_planner", {})
    
    prompt = state["prompt"]
    analysis = state.get("analysis", [])
    circuit_type = state.get("circuit_type", "unknown")
    primary_ic = state.get("primary_ic")
    requires_mcu = state.get("requires_mcu", circuit_type in ("mcu_based", "mixed"))
    
    # 1. Determine board type
    board_type = _determine_board_type(prompt, analysis)
    _emit(config, "agent:log", {"message": f"  Board type: {board_type}"})
    
    # 2. Extract primary MCU — only for MCU-based circuits
    primary_mcu = ""
    mcu_platform = "none"
    
    if requires_mcu:
        primary_mcu = _extract_mcu_from_analysis(analysis, prompt)
        if not primary_mcu:
            # Only default to ESP32-C3 if the circuit genuinely needs an MCU
            # and no MCU was identified. For IC-based circuits, this should not happen.
            primary_mcu = "ESP32-C3"
            _emit(config, "agent:log", {
                "message": f"  No MCU identified in prompt — defaulting to {primary_mcu}"
            })
        else:
            _emit(config, "agent:log", {"message": f"  Primary MCU: {primary_mcu}"})
        
        # 3. Determine platform from MCU
        mcu_lower = primary_mcu.lower()
        if "esp32" in mcu_lower or "esp8266" in mcu_lower:
            mcu_platform = "espressif"
        elif "stm32" in mcu_lower:
            mcu_platform = "st"
        elif "rp2040" in mcu_lower or "rp2350" in mcu_lower:
            mcu_platform = "raspberry_pi"
        elif "atmega" in mcu_lower or "attiny" in mcu_lower:
            mcu_platform = "microchip"
        else:
            mcu_platform = "unknown"
    else:
        # Non-MCU circuit: no MCU selection needed
        _emit(config, "agent:log", {
            "message": f"  Circuit type: {circuit_type}" + (f" (primary IC: {primary_ic})" if primary_ic else "") + " — no MCU needed"
        })
    
    # 4. Get capabilities provided by this board type
    provides = get_provides(board_type)
    _emit(config, "agent:log", {
        "message": f"  Provides: {', '.join(k for k, v in provides.items() if v)}"
    })
    
    # 5. Freeze architecture
    result = {
        "architecture_frozen": True,
        "board_type": board_type,
        "primary_mcu": primary_mcu,
        "mcu_platform": mcu_platform,
        "provides": provides,
    }
    
    if requires_mcu:
        emit_tool_event(config, "Architecture Planner", "completed",
                        f"Board type: {board_type}, MCU: {primary_mcu}")
        emit_assistant_message(config, f"Architecture locked: {board_type} board with {primary_mcu} MCU.")
    else:
        ic_info = f" (IC: {primary_ic})" if primary_ic else ""
        emit_tool_event(config, "Architecture Planner", "completed",
                        f"Board type: {board_type}, circuit: {circuit_type}{ic_info}")
        emit_assistant_message(config, f"Architecture locked: {board_type} board, {circuit_type} circuit{ic_info}.")
    
    return _stage_result(state, "architecture_planner", result)
