import json
import re

from agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER
from agent.utils import _emit, _check_stage_contract, _stage_result, _call_llm, _clean_json


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
                    "example_components": examples[:3],
                })
    if not analysis:
        analysis.append({
            "subsystem": "Microcontroller",
            "function": "Main controller",
            "example_components": ["ESP32-C3", "RP2040", "STM32G030"],
        })
        analysis.append({
            "subsystem": "Sensor",
            "function": "Input sensing",
            "example_components": ["DS18B20", "BME280", "TMP117"],
        })
    return analysis


def analyze_node(state, config):
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
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
    if not analysis:
        analysis = _fallback_analysis(state["prompt"])
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " +
                   ", ".join(a.get("subsystem", "?") for a in analysis)
    })
    return _stage_result(state, "analyze", {"analysis": analysis})
