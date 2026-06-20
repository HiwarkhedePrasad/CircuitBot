import json
import re

from agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER
from agent.utils import _emit, _emit_activity, _check_stage_contract, _stage_result, _call_llm, _clean_json


_SUBSYSTEM_KEYWORDS = [
    (r"(microcontroller|mcu|esp\d+|arduino|raspberry)", "Microcontroller", ["ESP32-C3", "RP2040", "STM32G030"]),
    (r"(sensor|temperature|humidity|pressure)", "Sensor", ["DS18B20", "DHT22", "BME280", "TMP117"]),
    (r"(display|screen|oled|lcd|tft|e-ink)", "Display", ["SSD1306 OLED", "SH1106", "ST7789"]),
    (r"(led|indicator|status\s*light)", "Status Indicator", ["LED", "LED green"]),
    (r"(usb[- ]?c|type[- ]?c|usb[_-]c)", "USB Interface", ["USB-C connector", "USBLC6-2SC6"]),
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


def analyze_node(state, config):
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
    _emit_activity(config, "analyze", "Analyze Design", "start")
    contract = _check_stage_contract("analyze", state, ["prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "analyze", {"analysis": []})
    try:
        text = _call_llm(ANALYZE_SYSTEM, ANALYZE_USER.format(prompt=state["prompt"]), stage="analyze")
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
                        "function": f"{a} for {state['prompt'][:60].strip()}...",
                        "bus": _detect_bus(a, ""),
                        "example_components": [],
                    })
        return out

    if not analysis:
        analysis = _fallback_analysis(state["prompt"])
    else:
        analysis = _normalize(analysis) or _fallback_analysis(state["prompt"])
    # Auto-inject Microcontroller subsystem when the design clearly needs
    # programmatic control but the user didn't explicitly ask for an MCU.
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
    subsystems = [a.get("subsystem", "?") for a in analysis]
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " + ", ".join(subsystems)
    })
    _emit_activity(config, "analyze", "Analyze Design", "update", kind="detection", detail=[f"Detected {s}" for s in subsystems])
    _emit_activity(config, "analyze", "Analyze Design", "done")
    return _stage_result(state, "analyze", {"analysis": analysis})
