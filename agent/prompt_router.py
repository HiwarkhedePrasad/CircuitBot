import json
import re

from agent.utils import _call_llm, _clean_json

PROMPT_ROUTER_SYSTEM = """You are a routing classifier for an AI PCB design assistant.
Classify the user's intent into exactly one of these categories:

1. add_component — User wants to ADD a specific component to the board.
   Examples: "add a 10k resistor", "I need an ATMega328P", "place an LED with resistor",
   "add a 100nF capacitor", "insert a USB-C connector"

2. design_pipeline — User wants a full circuit/PCB design generated from scratch.
   Examples: "design a fan controller PCB", "create a power supply circuit",
   "build a multi-channel environmental monitor", "make a USB-to-UART adapter",
   "design a board with an ESP32 and temperature sensor"

3. modify_design — User wants to MODIFY an existing design. This is for changes to a design that already exists.
   Examples: "change R1 to 10k", "swap U1 for MCP1700", "add a bypass cap on VCC",
   "remove R3", "connect LED to pin 13 instead", "make the power traces wider",
   "update the resistor value", "replace the capacitor", "adjust the netlist"
   The response must include: modification_type (value_change, part_swap, add_component, remove_component, net_modify, reroute), target (component ref or net name), and value (new value/part/details).

4. component_query — User wants INFORMATION about a component, not to add it.
   Examples: "find me a temperature sensor for I2C", "what's a DS18B20",
   "show me BME280 specs", "search for a 5V regulator", "tell me about the ESP32-C3"

5. design_query — User wants to know about the CURRENT SCHEMATIC on the canvas.
   Examples: "what components are on the canvas", "is this LED connected",
   "which pins are unconnected", "what does R1 connect to", "how many resistors are there",
   "what is the value of C1", "show me the nets", "what's on the schematic"

6. help — User needs assistance or information about the tool itself.
   Examples: "what can you do", "how does this work", "help me get started",
   "what commands are available", "show me how to use this", "capabilities"

7. other — Anything else that doesn't fit the above categories.
   Examples: "hello", "hi", "good morning", "thanks", random text

Return ONLY a JSON object with these exact keys:
- "intent": one of "add_component", "design_pipeline", "modify_design", "component_query", "design_query", "help", "other"
- "confidence": float between 0.0 and 1.0
- "reasoning": brief string explaining the classification
- "extracted_components": list of strings — part numbers or component names mentioned
- "modification_type": (only for modify_design) one of "value_change", "part_swap", "add_component", "remove_component", "net_modify", "reroute"
- "target": (only for modify_design) object with "ref" (component ref like R1) or "net" (net name like VCC)
- "value": (only for modify_design) object with the new value, part, or description

IMPORTANT RULES:
- If the user asks to MODIFY, CHANGE, UPDATE, SWAP, REPLACE, REMOVE, or ADJUST something in an existing design, use "modify_design".
- If the user asks to DESIGN or CREATE something with multiple parts/functions (e.g., "design a fan controller with sensors", "make a power supply with USB"), use "design_pipeline".
- If the user asks to ADD, PLACE, INSERT, or PUT a specific part, use "add_component".
- If the user asks for multiple specific parts (e.g., "add a 10k resistor and an LED"), still use "add_component" and list both in extracted_components.
- "add_component" is for component-level requests. "design_pipeline" is for system-level design requests.
- When in doubt, prefer "design_pipeline" over "add_component" for complex descriptions.
- For greetings, small talk, or ambiguous input, use "other" with low confidence.

No markdown, no explanation, just valid JSON."""

PROMPT_ROUTER_USER = """Classify this user message:
"{text}"

Return only the JSON object with intent, confidence, reasoning, and extracted_components."""

KEYWORD_FALLBACKS = [
    (r"\b(add|place|insert|put|include)\b.*\b(resistor|capacitor|led|diode|ic|chip|connector|sensor|header|terminal|transistor|fet|mcu|microcontroller|regulator|inductor|button|switch)\b",
     "add_component", 0.7),
    (r"\b(add|place|insert|put)\b", "add_component", 0.6),
    (r"\b(design|create|build|make|generate|develop|produce)\b.*\b(pcb|circuit|board|schematic|device|system|controller|monitor|supply|driver|module)\b",
     "design_pipeline", 0.8),
    (r"\b(design|create|build|make|generate)\b", "design_pipeline", 0.6),
    (r"\b(change|set|update|swap|replace|modify|adjust)\b.*\b(to|for|from|value|width|size)\b",
     "modify_design", 0.7),
    (r"\b(change|set|update|swap|replace|modify|adjust)\b", "modify_design", 0.65),
    (r"\b(remove|delete|drop)\b.*\b(component|resistor|capacitor|led|ic|r\d|c\d|u\d)\b",
     "modify_design", 0.7),
    (r"\b(remove|delete|drop)\b", "modify_design", 0.6),
    (r"\b(connect|route|wire)\b.*\b(to|from|instead|pin)\b",
     "modify_design", 0.65),
    (r"\b(why|how come|too many|duplicate|remove|get rid|fix|wrong)\b.*\b(esp|esp32|module|mcu|connector|cap|resistor|led)\b",
     "modify_design", 0.7),
    (r"\b(why|how come|too many|duplicate)\b",
     "modify_design", 0.5),
    (r"\b(find|search|look|show|tell|what is|what's)\b.*\b(component|part|sensor|ic|chip|resistor|capacitor)\b",
     "component_query", 0.7),
    (r"\b(what|which|how many|is|are)\b.*\b(on|in|the)\b.*\b(canvas|schematic|board|circuit)\b",
     "design_query", 0.8),
    (r"\b(is|are)\b.+\b(connected|unconnected|missing)\b",
     "design_query", 0.7),
    (r"\bwhat\b.*\b(connect|connects|net|wire)\b",
     "design_query", 0.7),
    (r"\b(show|list|describe|tell me about)\b.*\b(the |all )?(components|nets|pins|connections|schematic)\b",
     "design_query", 0.7),
    (r"\b(help|what can you do|how does|capabilities|command|guide|tutorial)\b",
     "help", 0.8),
]


def _component_complexity_score(text: str) -> int:
    text_lower = text.lower()
    score = 0
    component_groups = [
        r"\besp\d+\b|\bmicrocontroller\b|\bmcu\b|\brp2040\b|\bstm32\b|\batmega\b",
        r"\bbutton\b|\bswitch\b|\bencoder\b",
        r"\bled\b|\bstatus\s*led\b|\bindicator\b",
        r"\bsensor\b|\btemperature\b|\bhumidity\b|\bdisplay\b",
        r"\busb\b|\bconnector\b|\bheader\b|\bterminal\b",
        r"\bpower\b|\bregulator\b|\bbuck\b|\bboost\b|\bldo\b",
    ]
    for pattern in component_groups:
        if re.search(pattern, text_lower):
            score += 1
    if re.search(r"\bwith\b|\band\b|,", text_lower):
        score += 1
    return score


def _should_force_design_pipeline(text: str) -> bool:
    text_lower = text.lower().strip()
    if not text_lower:
        return False
    if re.search(r"\b(add|place|insert|put)\b", text_lower):
        return False
    if re.search(r"\b(change|update|swap|replace|modify|remove|delete|reroute|connect)\b", text_lower):
        return False
    if _component_complexity_score(text_lower) >= 3:
        return True
    return False


def _keyword_fallback(text: str) -> dict:
    if _should_force_design_pipeline(text):
        return {
            "intent": "design_pipeline",
            "confidence": 0.85,
            "reasoning": "Complex multi-part request should start full design pipeline",
            "extracted_components": [],
        }
    text_lower = text.lower().strip()
    for pattern, intent, confidence in KEYWORD_FALLBACKS:
        if re.search(pattern, text_lower):
            extracted = []
            part_pattern = r"\b([A-Za-z0-9]+(?:[-][A-Za-z0-9]+)*\d+[A-Za-z0-9]*)\b"
            extracted = re.findall(part_pattern, text)
            return {
                "intent": intent,
                "confidence": confidence,
                "reasoning": f"Keyword fallback matched: {pattern[:50]}...",
                "extracted_components": extracted[:5],
            }
    return {
        "intent": "other",
        "confidence": 0.3,
        "reasoning": "No keyword pattern matched",
        "extracted_components": [],
    }


def route_prompt(text: str) -> dict:
    if not text or not text.strip():
        return {
            "intent": "other",
            "confidence": 0.0,
            "reasoning": "Empty input",
            "extracted_components": [],
        }

    try:
        raw = _call_llm(PROMPT_ROUTER_SYSTEM, PROMPT_ROUTER_USER.format(text=text), stage="prompt_router")
        raw = _clean_json(raw)
        result = json.loads(raw) if raw else {}
        if not isinstance(result, dict):
            raise ValueError("LLM did not return a dict")
        result.setdefault("extracted_components", [])
        if result.get("intent") not in ("add_component", "design_pipeline", "modify_design", "component_query", "design_query", "help", "other"):
            raise ValueError(f"Unknown intent: {result.get('intent')}")
        if result.get("intent") == "add_component" and _should_force_design_pipeline(text):
            result["intent"] = "design_pipeline"
            result["confidence"] = max(float(result.get("confidence", 0) or 0), 0.85)
            result["reasoning"] = "Upgraded to design_pipeline because the request describes a multi-part design"
            result["extracted_components"] = []
        return result
    except Exception as e:
        print(f"Prompt router LLM call failed, using keyword fallback: {e}")
        return _keyword_fallback(text)
