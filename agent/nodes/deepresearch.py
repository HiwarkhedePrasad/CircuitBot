"""Deep Research node — thorough web research for each subsystem.

This node runs after analyze_node and before research_node.  It takes the
analysis (list of subsystems) and does targeted web research for each one,
producing structured recommendations that ground component selection in real
reference designs, datasheets, and design patterns.

Key outputs stored in state:
  - deep_research_results: list[dict] — structured per-subsystem research
  - web_research_results (enriched): existing field updated with deeper summaries
"""

import json
from uuid import uuid4

from agent.deep_search import deep_search_parallel
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _call_llm, _clean_json,
)


# Per-subsystem search query templates — targeted, grounded searches
_QUERY_TEMPLATES: dict[str, list[str]] = {
    "Microcontroller": [
        "reference design ESP32-C3 STM32 RP2040 minimum circuit schematic",
        "ESP32-C3 recommended external components power decoupling schematic",
        "ESP32-C3 vs RP2040 vs STM32 comparison for IoT projects 2026",
    ],
    "Sensor": [
        "DS18B20 temperature sensor wiring pull-up resistor value reference design",
        "BME280 I2C humidity sensor schematic example circuit design guide",
        "I2C sensor common design mistakes pull-up voltage level issues",
    ],
    "Display": [
        "SSD1306 OLED I2C 128x64 schematic example circuit pull-up resistors",
        "OLED display power consumption decoupling capacitor schematic",
        "common OLED display wiring mistakes level shifting 3.3V 5V",
    ],
    "Power Regulation": [
        "AMS1117-3.3 LDO input output capacitor selection datasheet reference",
        "3.3V voltage regulator selection guide for ESP32 power consumption",
        "buck converter vs LDO choosing right regulator for battery project",
    ],
    "Battery Management": [
        "TP4056 LiPo charger schematic application circuit resistor settings",
        "MCP73831 Li-Ion charger reference design schematic current set",
        "LiPo battery protection circuit design bms esp32 low power",
    ],
    "Wireless Module": [
        "ESP32-C3 antenna matching network reference design pcb layout guide",
        "NRF24L01 external antenna schematic matching components values",
        "PCB antenna design tips for 2.4GHz IoT module impedance matching",
    ],
    "Power Input": [
        "USB-C receptacle schematic CC pin resistors 5.1k reference design",
        "USB-C power delivery schematic for 5V only simple circuit",
        "USBLC6-2SC6 ESD protection schematic USB data lines placement",
    ],
    "User Input": [
        "tactile switch pull-up resistor schematic debouncing capacitor value",
        "EC11 rotary encoder schematic pull-up wiring example circuit",
        "button input esp32 gpio protection schematic series resistor",
    ],
    "Audio Output": [
        "piezo buzzer driver transistor schematic microcontroller gpio",
        "passive buzzer vs active buzzer circuit differences driving method",
    ],
    "Status Indicator": [
        "LED current limiting resistor calculation 3.3V microcontroller gpio",
        "RGB LED common cathode vs anode schematic transistor driver",
    ],
    "Connectors": [
        "pin header connector best practices decoupling schematic layout",
        "JST connector battery wiring schematic protection circuit",
    ],
    "Temperature Sensor": [
        "DS18B20 temperature sensor wiring pull-up resistor value schematic",
        "TMP117 I2C temperature sensor high accuracy reference design",
        "multiple DS18B20 parasitic power mode wiring schematic long distance",
    ],
    "Humidity Sensor": [
        "DHT22 humidity sensor schematic pull-up resistor timing constraints",
        "BME280 temperature humidity pressure sensor i2c schematic example",
    ],
    "Accelerometer": [
        "MPU6050 accelerometer gyroscope I2C schematic decoupling capacitor",
        "LIS3DH accelerometer I2C SPI schematic interrupt wiring",
    ],
    "Motor Driver": [
        "L298N motor driver schematic flyback diode snubber circuit",
        "DRV8833 DC motor driver schematic current limit setting resistor",
        "TB6612FNG motor driver schematic vs L298N comparison",
    ],
    "USB-UART": [
        "CP2102N USB to UART bridge schematic example circuit decoupling",
        "CH340G USB serial adapter schematic typical application circuit",
        "FT232RL USB UART schematic 3.3V 5V level selection pin",
    ],
    "EEPROM": [
        "AT24C02 I2C EEPROM schematic pull-up resistor address pins",
        "W25Q128JV SPI flash memory schematic wiring CS WP HOLD pins",
    ],
    "Level Shifter": [
        "TXB0104 bidirectional level shifter schematic application notes",
        "BSS138 logic level converter 3.3V 5V schematic resistor values",
        "I2C level shifting best practices BSS378 MOSFET PCA9306",
    ],
    "ESD Protection": [
        "USBLC6-2SC6 ESD protection diode schematic USB D+ D- layout",
        "USB ESD protection best practices TVS diode placement routing",
        "TPD6S300A USB-C ESD protection schematic application",
    ],
}

# Default queries for unrecognized subsystems
_DEFAULT_QUERIES = [
    "reference design schematic example circuit {subsystem}",
    "{subsystem} recommended components wiring application note",
    "{subsystem} common design mistakes best practices pcb layout",
]


def _build_queries(analysis: list[dict]) -> list[str]:
    """Build targeted web search queries for each subsystem.

    Returns a flat list of queries — each gets its own web search.
    """
    queries: list[str] = []
    for sub in analysis:
        name = sub.get("subsystem", "")
        templates = _QUERY_TEMPLATES.get(name, _DEFAULT_QUERIES)
        for tpl in templates:
            formatted = tpl.format(subsystem=name)
            queries.append(formatted)
    return queries


def _build_queries_by_subsystem(analysis: list[dict]) -> list[dict]:
    """Build queries with subsystem tracking so results can be grouped.

    Returns: list of {subsystem: str, query: str} dicts.
    """
    items: list[dict] = []
    for sub in analysis:
        name = sub.get("subsystem", "")
        templates = _QUERY_TEMPLATES.get(name, _DEFAULT_QUERIES)
        for tpl in templates:
            formatted = tpl.format(subsystem=name)
            items.append({"subsystem": name, "query": formatted})
    return items


_RESEARCH_SYSTEM_PROMPT = """You are an expert electronics design researcher. 

Given the following web search results for a PCB subsystem, synthesize them into a
structured research summary.  Include:

1. **Recommended Components** — specific part numbers with key specs and why they work
2. **Design Patterns** — typical schematic patterns, pin connections, pull-up/down values
3. **Design Considerations** — common mistakes, thermal issues, layout tips, decoupling needs
4. **Reference Design Links** — any URLs to reference designs or application notes found

Focus on practical, buildable recommendations. Avoid vague suggestions — include
specific component values, resistor/capacitor sizes, and pin numbers where available.

If the search results are thin, state what could not be verified and fall back on
standard engineering practice (e.g., "Use 100nF decoupling per IC, 10uF bulk")."""


def _synthesize_subsystem(name: str, raw_summaries: list[str], config) -> dict:
    """Use the LLM to synthesize multiple search results into one structured summary."""
    combined = "\n\n---\n\n".join(
        f"Search result #{i+1}:\n{s}"
        for i, s in enumerate(raw_summaries) if s
    )

    prompt = (
        f"Research target: '{name}' subsystem\n\n"
        f"Web search results:\n{combined}\n\n"
        "Return a JSON object with exactly these keys:\n"
        '  "subsystem": str (the subsystem name),\n'
        '  "recommended_components": list of {{"part": str, "why": str}},\n'
        '  "design_patterns": list of str (key schematic patterns),\n'
        '  "key_values": list of str (pull-up values, cap values, etc.),\n'
        '  "design_considerations": list of str,\n'
        '  "reference_urls": list of str,\n'
        '  "summary": str (2-3 sentence overall summary)\n\n'
        "Return ONLY valid JSON, no markdown fences."
    )

    try:
        text = _call_llm(_RESEARCH_SYSTEM_PROMPT, prompt, stage="deepresearch")
        text = _clean_json(text)
        result = json.loads(text) if text else {}
        if isinstance(result, dict) and "subsystem" in result:
            return result
    except Exception as e:
        _emit(config, "agent:log", {
            "message": f"  LLM synthesis failed for '{name}': {e}"
        })

    return {
        "subsystem": name,
        "recommended_components": [],
        "design_patterns": [],
        "key_values": [],
        "design_considerations": [],
        "reference_urls": [],
        "summary": "Web research completed but could not be synthesized into structured data.",
    }


def deepresearch_node(state, config):
    """Run deep web research for each subsystem identified in analysis.

    Produces structured research data that grounds component selection in real
    reference designs, datasheets, and application notes.
    """
    _emit(config, "agent:thinking", {"message": "Researching each subsystem from multiple sources..."})
    emit_assistant_message(config, "Running deep web research for each subsystem...")
    emit_tool_event(config, "Deep Research", "running", "Researching subsystems...")

    contract = _check_stage_contract("deepresearch", state, ["analysis"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "deepresearch", {
            "deep_research_results": [],
        })

    analysis = state.get("analysis", [])
    if not analysis:
        _emit(config, "agent:log", {"message": "No subsystems to research."})
        return _stage_result(state, "deepresearch", {
            "deep_research_results": [],
        })

    # Build research queries, grouped by subsystem so we can synthesize per sub
    query_items = _build_queries_by_subsystem(analysis)
    queries = [item["query"] for item in query_items]
    # Map each query index back to its subsystem
    query_to_sub: list[str] = [item["subsystem"] for item in query_items]

    _emit(config, "agent:log", {
        "message": f"  {len(analysis)} subsystems, {len(queries)} search queries ({len(queries)//max(len(analysis),1)} avg per sub)"
    })

    # Track subsystem names for research event
    sub_names = [a.get("subsystem", "?") for a in analysis]
    emit_tool_event(config, "Deep Subsystem Research", "running",
                    f"Researching {len(sub_names)} subsystems on the web")

    # Run parallel web searches
    raw_results = deep_search_parallel(queries, config=config)

    # Group raw results by subsystem
    sub_raw: dict[str, list[str]] = {}
    for i, result in enumerate(raw_results):
        sub_name = query_to_sub[i] if i < len(query_to_sub) else "unknown"
        if sub_name not in sub_raw:
            sub_raw[sub_name] = []
        summary = result.get("summary", "") if isinstance(result, dict) else str(result)
        sub_raw[sub_name].append(summary)

    # Synthesize each subsystem's raw searches into structured research
    deep_results = []
    web_summaries = []
    for sub_name, summaries in sub_raw.items():
        _emit(config, "agent:log", {
            "message": f"  Synthesizing research for '{sub_name}' ({len(summaries)} sources)..."
        })
        synthesized = _synthesize_subsystem(sub_name, summaries, config)
        deep_results.append(synthesized)
        web_summaries.append({
            "subsystem": sub_name,
            "summary": synthesized.get("summary", ""),
        })

    _emit(config, "agent:log", {
        "message": f"  Deep research complete for {len(deep_results)}/{len(analysis)} subsystems"
    })

    emit_tool_event(config, "Deep Subsystem Research", "completed",
                    f"Research complete for {len(deep_results)} subsystems")
    emit_tool_event(config, "Deep Research", "completed",
                    f"Structured research data for {len(deep_results)} subsystems")
    emit_assistant_message(config, f"Deep research complete — gathered structured data for {len(deep_results)} subsystems.")

    return _stage_result(state, "deepresearch", {
        "deep_research_results": deep_results,
        "web_research_results": web_summaries,
    })
