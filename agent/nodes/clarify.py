"""Pre-generation clarification node — asks user targeted questions before designing.

Runs BEFORE analyze_node. Assesses whether the user's prompt has enough detail
to produce a good design. If not, emits clarification questions and blocks until
the user responds. Merges answers into an enhanced prompt.
"""

import json

from agent.prompts import CLARIFY_SYSTEM
from agent.pipeline_tracker import update_pipeline_stage
from agent.utils import _call_llm, _clean_json, _emit, emit_assistant_message, emit_tool_event

# Dimensions that make a prompt "specific enough"
_SPECIFIC_KEYWORDS = {
    "mcu": ["esp32", "rp2040", "stm32", "atmega", "attiny", "samd", "nrf", "pic", "fpga",
            "arduino", "raspberry pi", "teensy", "xiao", "feather"],
    "sensor": ["ds18b20", "bme280", "tmp117", "tmp102", "dht22", "thermistor", "ntc",
               "lis3dh", "mpu6050", "max30102", "hc-sr04", "photoresistor", "ir sensor"],
    "power": ["usb-c", "usb type-c", "lipo", "battery", "12v", "24v", "5v", "3.3v",
              "solar", "coin cell", "power supply", "dc jack", "barrel jack"],
    "connectivity": ["wifi", "ble", "bluetooth", "lora", "zigbee", "mqtt", "websocket",
                     "usb", "uart", "spi", "i2c", "can bus"],
    "display": ["oled", "lcd", "e-ink", "epaper", "tft", "led matrix", "7-segment",
                "neopixel", "ws2812", "display"],
}

# Keywords for non-MCU circuits: dedicated ICs, analog components, timers
_IC_BASED_KEYWORDS = [
    "ne555", "555 timer", "lm555", "icm7555", "lm358", "lm324", "lm741", "op-amp",
    "opamp", "lm7805", "lm7812", "lm317", "lm1117", "74hc", "74ls", "cd4017",
    "cd4026", "cd4049", "cd4066", "cd4069", "cd4070", "cd4081", "lm339", "lm393",
    "timer ic", "astable", "monostable", "bistable", "multivibrator",
    "op amp", "operational amplifier", "voltage regulator", "comparator",
]

# Keywords for analog-only circuits (no ICs needed)
_ANALOG_ONLY_KEYWORDS = [
    "rc circuit", "lc circuit", "rlc", "voltage divider", "filter", "low-pass",
    "high-pass", "band-pass", "attenuator", "impedance matching",
]


def _detect_circuit_type(prompt: str) -> str:
    """Detect whether the design needs an MCU, uses a dedicated IC, or is analog-only.

    Returns: "mcu_based", "ic_based", "analog_only", or "mixed"
    """
    lower = prompt.lower()

    has_mcu = any(kw in lower for kw in _SPECIFIC_KEYWORDS["mcu"])
    has_ic = any(kw in lower for kw in _IC_BASED_KEYWORDS)
    has_analog_only = any(kw in lower for kw in _ANALOG_ONLY_KEYWORDS)

    if has_mcu:
        return "mcu_based"
    if has_ic and not has_mcu:
        return "ic_based"
    if has_analog_only and not has_mcu and not has_ic:
        return "analog_only"
    return "mixed"  # default: assume MCU may be needed


def _extract_ic_from_prompt(prompt: str) -> str | None:
    """Extract the primary IC name from the prompt (e.g., 'NE555', 'LM358')."""
    import re
    ic_patterns = [
        (r'\b(NE555|LM555|ICM7555|TLC555|LMC555)\b', lambda m: m.group(1).upper()),
        (r'\b(LM358|LM324|LM741|TL072|TL082|OPA2134)\b', lambda m: m.group(1).upper()),
        (r'\b(LM7805|LM7812|LM7809|LM317|LM337|LM1117|AMS1117)\b', lambda m: m.group(1).upper()),
        (r'\b(74HC\d+|74LS\d+|CD40\d+|CD45\d+)\b', lambda m: m.group(1).upper()),
        (r'\b(LM339|LM393|LM311|MAX9117)\b', lambda m: m.group(1).upper()),
    ]
    upper = prompt.upper()
    for pattern, extractor in ic_patterns:
        m = re.search(pattern, upper)
        if m:
            return extractor(m)
    return None


def _count_specific_dimensions(prompt: str) -> int:
    """Count how many design dimensions are already specified."""
    lower = prompt.lower()
    count = 0
    for dim, keywords in _SPECIFIC_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            count += 1
    # IC-based or analog-only circuits count as "specific" (they don't need MCU questions)
    if any(kw in lower for kw in _IC_BASED_KEYWORDS) or any(kw in lower for kw in _ANALOG_ONLY_KEYWORDS):
        count += 1
    return count


def _assess_clarification(prompt: str) -> dict:
    """LLM-based assessment of what's missing from the prompt."""
    try:
        response = _call_llm(
            CLARIFY_SYSTEM,
            f"User prompt: {prompt}",
        )
        parsed = _clean_json(response)
        if isinstance(parsed, dict) and "needs_clarification" in parsed:
            return parsed
    except Exception as e:
        print(f"[clarify] LLM assessment failed: {e}")

    # Fallback: heuristic-based assessment
    return _heuristic_assessment(prompt)


def _heuristic_assessment(prompt: str) -> dict:
    """Fallback heuristic when LLM is unavailable."""
    lower = prompt.lower()
    questions = []

    # Detect circuit type — skip MCU question for IC-based or analog-only circuits
    circuit_type = _detect_circuit_type(prompt)
    is_ic_based = circuit_type in ("ic_based", "analog_only")

    # Check MCU — only ask if this is an MCU-based circuit
    has_mcu = any(kw in lower for kw in _SPECIFIC_KEYWORDS["mcu"])
    if not has_mcu and not is_ic_based:
        questions.append({
            "id": "q1",
            "question": "What MCU platform?",
            "options": ["ESP32 (WiFi/BLE)", "RP2040 (USB native)", "STM32 (low power)", "No preference"],
        })

    # Check sensor/input
    has_sensor = any(kw in lower for kw in _SPECIFIC_KEYWORDS["sensor"])
    if not has_sensor and any(kw in lower for kw in ("sensor", "measure", "monitor", "detect", "read")):
        questions.append({
            "id": "q2",
            "question": "What type of sensor/input?",
            "options": ["Digital (DS18B20, BME280)", "I2C (TMP117)", "Analog (thermistor)", "No preference"],
        })

    # Check power
    has_power = any(kw in lower for kw in _SPECIFIC_KEYWORDS["power"])
    if not has_power:
        questions.append({
            "id": "q3",
            "question": "How will you power it?",
            "options": ["USB-C", "Battery (LiPo)", "External supply", "No preference"],
        })

    # Check connectivity
    has_connectivity = any(kw in lower for kw in _SPECIFIC_KEYWORDS["connectivity"])
    if not has_connectivity:
        questions.append({
            "id": "q4",
            "question": "Connectivity needed?",
            "options": ["WiFi/BLE", "USB serial only", "No preference"],
        })

    # Check display
    has_display = any(kw in lower for kw in _SPECIFIC_KEYWORDS["display"])
    if not has_display:
        questions.append({
            "id": "q5",
            "question": "Any display or output?",
            "options": ["OLED display", "LED indicators", "None", "No preference"],
        })

    return {
        "needs_clarification": len(questions) > 0,
        "questions": questions[:5],  # max 5
    }


def _build_enhanced_prompt(original: str, answers: dict, questions: list) -> str:
    """Merge clarification answers into the original prompt."""
    if not answers:
        return original

    # Map answer keys to question text
    q_map = {q["id"]: q for q in questions}

    additions = []
    for q_id, answer in answers.items():
        if answer == "No preference" or not answer:
            continue
        q = q_map.get(q_id)
        if q:
            additions.append(f"  - {q['question'].rstrip('?')}: {answer}")

    if not additions:
        return original

    return f"{original.rstrip('.')}. Specific requirements:\n" + "\n".join(additions)


def clarify_node(state, config):
    """Assess prompt completeness and ask clarifying questions if needed.

    Returns enhanced prompt if user answers questions, or original prompt
    if prompt is already specific enough. Also detects circuit type.
    """
    prompt = state.get("prompt", "")

    # Detect circuit type early — this influences everything downstream
    circuit_type = _detect_circuit_type(prompt)
    primary_ic = _extract_ic_from_prompt(prompt)
    requires_mcu = circuit_type in ("mcu_based", "mixed")

    _emit(config, "agent:log", {
        "message": f"Circuit type: {circuit_type}" + (f", primary IC: {primary_ic}" if primary_ic else "")
    })

    # Count how specific the prompt already is
    specific_count = _count_specific_dimensions(prompt)

    # If prompt is already specific enough (3+ dimensions), skip clarification
    if specific_count >= 3:
        _emit(config, "agent:log", {
            "message": f"Prompt has {specific_count} specific dimensions — skipping clarification"
        })
        return {
            "clarification_needed": False,
            "clarification_questions": [],
            "clarification_answers": {},
            "circuit_type": circuit_type,
            "primary_ic": primary_ic,
            "requires_mcu": requires_mcu,
        }

    # Assess what's missing
    assessment = _assess_clarification(prompt)
    needs_clarification = assessment.get("needs_clarification", False)
    questions = assessment.get("questions", [])

    if not needs_clarification or not questions:
        _emit(config, "agent:log", {
            "message": "Clarification assessment: prompt is clear enough to proceed"
        })
        return {
            "clarification_needed": False,
            "clarification_questions": [],
            "clarification_answers": {},
            "circuit_type": circuit_type,
            "primary_ic": primary_ic,
            "requires_mcu": requires_mcu,
        }

    # Emit clarification questions to frontend
    configurable = config.get("configurable", {})
    emit = configurable.get("emit")

    if emit:
        emit("agent:clarify", {
            "message": "Before I generate, a few quick questions to get it right:",
            "questions": questions,
        })

    emit_assistant_message(config, "I need a few details before designing. Please answer the questions below.")
    emit_tool_event(config, "Clarification", "running",
                    f"Asking {len(questions)} question(s) to refine the design")

    # Block on user response
    clarify_event = configurable.get("clarify_event")
    clarify_result = configurable.get("clarify_result")

    if clarify_event is not None:
        update_pipeline_stage(config, "waiting", "Awaiting requirement answers")
        clarify_event.wait(timeout=300)  # 5 minutes timeout
        update_pipeline_stage(config, "running", "Processing requirement answers")

    answers = {}
    if clarify_result is not None:
        answers = clarify_result.get("answers", {})

    # Build enhanced prompt
    enhanced_prompt = _build_enhanced_prompt(prompt, answers, questions)

    _emit(config, "agent:log", {
        "message": f"Clarification complete: {len(answers)} answer(s) received"
    })

    if answers:
        emit_tool_event(config, "Clarification", "completed",
                        f"Enhanced prompt with {len(answers)} clarification(s)")
    else:
        emit_tool_event(config, "Clarification", "completed",
                        "No answers received — proceeding with original prompt")
        enhanced_prompt = prompt

    return {
        "prompt": enhanced_prompt,
        "clarification_needed": True,
        "clarification_questions": questions,
        "clarification_answers": answers,
        "circuit_type": circuit_type,
        "primary_ic": primary_ic,
        "requires_mcu": requires_mcu,
    }
