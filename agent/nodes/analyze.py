import json
import re

from agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER
from agent.templates.matcher import find_best_template, get_library_filter
from agent.utils import _extract_part_numbers
from agent.utils import _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result, _call_llm, _clean_json

TEMPLATE_CONFIDENCE_THRESHOLD = 0.6


_SUBSYSTEM_KEYWORDS = [
    (r"(microcontroller|mcu|esp\d+|arduino|raspberry)", "Microcontroller", ["ESP32-C3", "RP2040", "STM32G030"]),
    (r"(sensor|temperature|humidity|pressure)", "Sensor", ["DS18B20", "DHT22", "BME280", "TMP117"]),
    (r"(display|screen|oled|lcd|tft|e-ink)", "Display", ["SSD1306 OLED", "SH1106", "ST7789"]),
    (r"(led|indicator|status\s*light)", "Status Indicator", ["LED", "LED green"]),
    (r"(usb[- ]?c|type[- ]?c|usb[_-]c)", "Power Input", ["USB_C_Receptacle_USB2.0", "USBLC6-2SC6"]),
    (r"(power|regulator|buck|boost|ldo|voltage)", "Power Regulation", ["AMS1117-3.3", "MCP73831", "TPS63000"]),
    (r"(battery|charger|li[- ]?ion|lipo|18650)", "Battery Management", ["MCP73831", "TP4056", "BQ24040"]),
    (r"(wifi|bluetooth|wireless|nrf|esp)", "Wireless Module", ["ESP32-C3", "NRF24L01", "CC1101"]),
    (r"(button|switch|touch|encoder)", "User Input", ["Tactile switch", "EC11 encoder"]),
    (r"(buzzer|speaker|audio|piezo)", "Audio Output", ["Buzzer", "Piezo"]),
    (r"(resistor|capacitor|inductor|diode)", "Passive Components", []),
    (r"(connector|header|pin|terminal)", "Connectors", []),
]


def _fallback_analysis(prompt: str) -> list:
    prompt_lower = prompt.lower()
    seen = set()
    analysis = []

    # Check if this is an IC-based or analog-only circuit first
    from agent.nodes.clarify import _detect_circuit_type, _extract_ic_from_prompt
    circuit_type = _detect_circuit_type(prompt)
    primary_ic = _extract_ic_from_prompt(prompt)

    if circuit_type in ("ic_based", "analog_only"):
        # For IC-based circuits, don't default to MCU+Sensor
        # Just match what's actually in the prompt
        for pattern, subsystem, examples in _SUBSYSTEM_KEYWORDS:
            if re.search(pattern, prompt_lower):
                if subsystem not in seen:
                    seen.add(subsystem)
                    analysis.append({
                        "subsystem": subsystem,
                        "function": f"{subsystem} for {prompt[:60].strip()}...",
                        "bus": _detect_bus(subsystem, ""),
                        "example_components": examples[:3],
                    })
        if not analysis:
            # Minimal fallback for unrecognized IC-based circuits
            analysis.append({
                "subsystem": "Passive Components",
                "function": f"Resistors and capacitors for {prompt[:60].strip()}...",
                "bus": "analog",
                "example_components": [],
            })
        return analysis

    # MCU-based circuits: original logic
    for pattern, subsystem, examples in _SUBSYSTEM_KEYWORDS:
        if re.search(pattern, prompt_lower):
            if subsystem not in seen:
                seen.add(subsystem)
                analysis.append({
                    "subsystem": subsystem,
                    "function": f"{subsystem} for {prompt[:60].strip()}...",
                    "bus": _detect_bus(subsystem, ""),
                    "example_components": examples[:3],
                })
    if not analysis:
        analysis.append({
            "subsystem": "Microcontroller",
            "function": "Main controller",
            "bus": "any",
            "example_components": ["ESP32-C3", "RP2040", "STM32G030"],
        })
        analysis.append({
            "subsystem": "Sensor",
            "function": "Input sensing",
            "bus": "I2C",
            "example_components": ["DS18B20", "BME280", "TMP117"],
        })
    return analysis


def _detect_bus(subsystem: str, function: str) -> str:
    bus_map = {
        "i2c": "I2C", "sda": "I2C", "scl": "I2C",
        "spi": "SPI", "mosi": "SPI", "miso": "SPI",
        "1-wire": "1-Wire", "onewire": "1-Wire", "one wire": "1-Wire",
        "uart": "UART", "serial": "UART", "tx": "UART", "rx": "UART",
        "usb": "USB", "i2s": "I2S", "can": "CAN",
    }
    text = (subsystem + " " + function).lower()
    for keyword, bus in bus_map.items():
        if keyword in text:
            return bus
    # Default by subsystem type
    sub_lower = subsystem.lower()
    if "display" in sub_lower or "oled" in sub_lower:
        return "I2C"
    if "sensor" in sub_lower or "temperature" in sub_lower or "humidity" in sub_lower:
        return "I2C"
    if "microcontroller" in sub_lower or "mcu" in sub_lower:
        return "any"
    return "any"


def _apply_user_part_intent(analysis: list[dict], prompt: str) -> list[dict]:
    user_parts = _extract_part_numbers(prompt)
    if not user_parts:
        return analysis
    prompt_upper = prompt.upper()
    for item in analysis:
        subsystem = (item.get("subsystem", "") or "").lower()
        examples = item.get("example_components", [])
        if not isinstance(examples, list):
            examples = [examples] if examples else []
        matched_parts = []
        for part in user_parts:
            part_upper = part.upper()
            if "sensor" in subsystem and any(token in prompt_upper for token in (part_upper, "SENSOR", "TEMPERATURE", "HUMIDITY", "PRESSURE")):
                matched_parts.append(part)
            elif "microcontroller" in subsystem and any(token in prompt_upper for token in (part_upper, "MCU", "MICROCONTROLLER")):
                matched_parts.append(part)
            elif "power" in subsystem and any(token in prompt_upper for token in (part_upper, "BUCK", "BOOST", "LDO", "REGULATOR", "POWER")):
                matched_parts.append(part)
            elif "wireless" in subsystem and any(token in prompt_upper for token in (part_upper, "WIRELESS", "WIFI", "BLUETOOTH", "RF")):
                matched_parts.append(part)
        if matched_parts:
            item["example_components"] = list(dict.fromkeys(matched_parts + examples))
    return analysis


def analyze_node(state, config):
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
    emit_assistant_message(config, "Parsing the design prompt to identify subsystems and requirements...")
    emit_tool_event(config, "Analyze Design", "running", "Parsing design prompt...")
    contract = _check_stage_contract("analyze", state, ["prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "analyze", {"analysis": []})

    prompt = state["prompt"]

    # ── Phase 1: Try deterministic template matching (zero LLM calls) ──
    match = find_best_template(prompt)
    if match and match.is_confident(threshold=TEMPLATE_CONFIDENCE_THRESHOLD):
        _emit(config, "agent:log", {
            "message": f"  Template matched: '{match.name}' (confidence={match.confidence:.2f}) — using template subsystems"
        })
        analysis = match.subsystems
        template_id = match.template_id
        template_nets = match.nets
        # Add library_filter to each subsystem from the template
        for sub in analysis:
            sub.setdefault("library_filter", get_library_filter(sub))
            sub.setdefault("bus", _detect_bus(sub.get("subsystem", ""), sub.get("function", "")))
        _emit(config, "agent:log", {
            "message": f"  Template '{template_id}' provides {len(analysis)} subsystems"
        })
    else:
        # ── Phase 2: Fall back to LLM analysis ──
        if match:
            _emit(config, "agent:log", {
                "message": f"  Template match too weak ({match.name}, confidence={match.confidence:.2f}) — using LLM"
            })
        try:
            text = _call_llm(ANALYZE_SYSTEM, ANALYZE_USER.format(prompt=prompt), stage="analyze")
        except Exception as e:
            print(f"LLM analysis failed, using keyword fallback: {e}")
            text = ""
        text = _clean_json(text)
        try:
            analysis = json.loads(text) if text else []
        except json.JSONDecodeError:
            print(f"Failed to parse analysis JSON: {text[:200]}")
            analysis = []
        def _normalize(items):
            out = []
            if isinstance(items, dict):
                for v in items.values():
                    out.extend(_normalize(v))
            elif isinstance(items, list):
                for a in items:
                    if isinstance(a, dict):
                        a.setdefault("bus", _detect_bus(a.get("subsystem", ""), a.get("function", "")))
                        out.append(a)
                    elif isinstance(a, str):
                        out.append({
                            "subsystem": a,
                            "function": f"{a} for {prompt[:60].strip()}...",
                            "bus": _detect_bus(a, ""),
                            "example_components": [],
                        })
            return out

        if not analysis:
            analysis = _fallback_analysis(prompt)
        else:
            analysis = _normalize(analysis) or _fallback_analysis(prompt)
        template_id = None
        template_nets = []

    analysis = _apply_user_part_intent(analysis, prompt)

    # Auto-inject Microcontroller subsystem when the design clearly needs
    # programmatic control but the user didn't explicitly ask for an MCU.
    # Gate this on circuit_type — don't auto-inject MCU for IC-based or analog-only circuits.
    from agent.nodes.clarify import _detect_circuit_type
    circuit_type = state.get("circuit_type", _detect_circuit_type(prompt))
    requires_mcu = state.get("requires_mcu", circuit_type in ("mcu_based", "mixed"))

    if requires_mcu:
        _IMPLIES_MCU = {"Sensor", "Display", "Status Indicator", "User Input", "Wireless Module"}
        sub_names = {a.get("subsystem", "") for a in analysis}
        if "Microcontroller" not in sub_names and sub_names & _IMPLIES_MCU:
            analysis.insert(0, {
                "subsystem": "Microcontroller",
                "function": "Main controller for the system",
                "bus": "any",
                "example_components": ["ESP32-C3", "RP2040", "STM32G030"],
            })
            _emit(config, "agent:log", {
                "message": "  Auto-added Microcontroller subsystem (implied by design requirements)"
            })

    # Strip MCU subsystems for IC-based or analog-only circuits
    # The LLM may incorrectly add "Microcontroller" even for simple timer circuits
    if circuit_type in ("ic_based", "analog_only"):
        mcu_keywords = ("microcontroller", "mcu", "processor", "cpu", "microprocessor")
        original_count = len(analysis)
        analysis = [
            a for a in analysis
            if not any(kw in (a.get("subsystem", "") or "").lower() for kw in mcu_keywords)
        ]
        removed = original_count - len(analysis)
        if removed:
            _emit(config, "agent:log", {
                "message": f"  Filtered {removed} MCU subsystem(s) for {circuit_type} circuit"
            })

    subsystems = [a.get("subsystem", "?") for a in analysis]
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " + ", ".join(subsystems)
    })
    emit_tool_event(config, "Analyze Design", "completed", f"{len(analysis)} subsystems found")
    emit_assistant_message(config, f"Identified {len(analysis)} subsystems: {', '.join(subsystems)}.")

    result = {"analysis": analysis}
    if template_id:
        result["template_id"] = template_id
        result["template_nets"] = template_nets
    return _stage_result(state, "analyze", result)
