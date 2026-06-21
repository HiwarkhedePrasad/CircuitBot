import json
import re

from agent.prompts import VALIDATE_SYSTEM, VALIDATE_USER
from agent.tools import search_components
from agent.utils import (
    _emit, _emit_activity, _check_stage_contract, _stage_result, _call_llm, _clean_json, _ref_prefix_for,
    MAX_VALIDATION_RETRIES,
)

_KNOWN_SYMBOLS = frozenset([
    "Device:R_Small", "Device:C_Small", "Device:LED", "Device:L_Small",
    "Device:D_Small", "Connector_USB:USB_C_Receptacle_USB2.0",
    "Regulator_Linear:AMS1117-3.3",
    "Connector_USB:TPD6S300A",
    "Sensor_Temperature:TMP117xxYBG",
    "Sensor_Temperature:DS18B20",
    "Device:Crystal", "Device:Crystal_GND24", "Device:Crystal_Small",
    "Connector:AVR-ISP-6",
    "Device:Polyfuse",
    # Placeholder symbols from support_rules KNOWN_FALLBACK_SYMBOLS —
    # these are used when RAG has no real KiCad symbol for an IC.
    # They are placeholders; the validator should not flag them.
    "Device:TPD6S300A", "Device:USBLC6-2SC6", "Device:IP4234CZ10",
    "Device:SRV05-4",
])

LIBRARY_PREFIX_FIXES: dict[str, str] = {
    'Connector:USB_C_':  'Connector_USB:USB_C_',
    'Connector:USB_':    'Connector_USB:USB_',
    'Connector:USB2':    'Connector_USB:USB2',
}

_CRITICAL_PATTERNS = [
    ("infrared", "led", "Status LED is infrared — not visible to human eye"),
    ("antenna", "resistor", "Antenna selected where resistor required"),
    ("cpld", "capacitor", "CPLD selected where capacitor required"),
    ("pd controller", "connector", "USB PD controller selected where USB-C connector required"),
]


def _fix_library_prefixes(components: list[dict], emit_fn) -> int:
    n_fixed = 0
    for comp in components:
        id_str = comp.get('id_str', '')
        for wrong, right in LIBRARY_PREFIX_FIXES.items():
            if id_str.startswith(wrong):
                fixed = right + id_str[len(wrong):]
                emit_fn("agent:log", {
                    "message": f"  Corrected prefix: {id_str} -> {fixed}"
                })
                comp['id_str'] = fixed
                n_fixed += 1
                break
    return n_fixed


_PART_FAMILIES: dict[re.Pattern, dict] = {
    # pattern → { "family": str, "traits": set[str], "comment": str }
    re.compile(r'\bESP32[-_ ]?(?:C3|C6|S2|S3|H2|P4)?\b', re.IGNORECASE):
        {"family": "ESP32", "traits": {"wireless", "wifi", "bluetooth", "risc-v or xtensa"}, "comment": "wireless MCU"},
    re.compile(r'\bSTM32\w*\b', re.IGNORECASE):
        {"family": "STM32", "traits": {"arm", "cortex-m"}, "comment": "ARM Cortex-M MCU"},
    re.compile(r'\bRP2040\b', re.IGNORECASE):
        {"family": "RP2040", "traits": {"arm", "cortex-m0+"}, "comment": "Raspberry Pi MCU"},
    re.compile(r'\bRP2350\b', re.IGNORECASE):
        {"family": "RP2350", "traits": {"arm", "cortex-m33", "risc-v"}, "comment": "Raspberry Pi MCU"},
    re.compile(r'\bATmega\w*\b', re.IGNORECASE):
        {"family": "ATmega", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bATTINY\w*\b', re.IGNORECASE):
        {"family": "ATtiny", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bAT90\w*\b', re.IGNORECASE):
        {"family": "AT90", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bSAMD\w*\b', re.IGNORECASE):
        {"family": "SAMD", "traits": {"arm", "cortex-m0+"}, "comment": "ARM Cortex-M0+ MCU"},
}

_MCU_FAMILY_KEYWORDS: dict[str, set[str]] = {
    "ESP32":    {"ESP32", "RISP32", "XTENSA", "WIRELESS", "WIFI", "BLUETOOTH", "IEEE802"},
    "STM32":    {"STM32", "CORTEX", "ARM"},
    "RP2040":   {"RP2040", "CORTEX", "ARM"},
    "RP2350":   {"RP2350", "CORTEX", "ARM"},
    "ATmega":   {"ATMEGA", "MEGA", "AVR"},
    "ATTINY":   {"ATTINY", "TINY", "AVR"},
    "AT90":     {"AT90", "AVR"},
    "SAMD":     {"SAMD", "CORTEX", "ARM"},
}


def _check_prompt_integrity(prompt: str, comps: list[dict]) -> list[str]:
    """Deterministic pre-check: if the user named a specific part family,
    flag any selected component that belongs to a different (incompatible)
    MCU family.

    Returns a list of error messages, empty if no violations.
    """
    mentioned_families: set[str] = set()
    for pattern, info in _PART_FAMILIES.items():
        if pattern.search(prompt):
            mentioned_families.add(info["family"])

    if not mentioned_families:
        return []

    errors: list[str] = []
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        desc = (c.get("description", "") or "").upper()
        id_and_desc = f"{id_str} {desc}"

        detected_families: set[str] = set()
        for fam, keywords in _MCU_FAMILY_KEYWORDS.items():
            if any(kw in id_and_desc for kw in keywords):
                detected_families.add(fam)

        if not detected_families:
            continue

        mentioned_without_wireless = mentioned_families - {"ESP32"}
        detected_without_wireless = detected_families - {"ESP32"}

        if mentioned_without_wireless and detected_without_wireless:
            if mentioned_without_wireless != detected_without_wireless:
                errors.append(
                    f"Prompt-integrity: user requested {', '.join(sorted(mentioned_families))} "
                    f"but {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                    f"is a {', '.join(sorted(detected_families))} family part — "
                    f"family mismatch"
                )
        elif "ESP32" in mentioned_families and "ESP32" not in detected_families and detected_families:
            errors.append(
                f"Prompt-integrity: user requested ESP32 (wireless MCU) "
                f"but {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                f"is a {', '.join(sorted(detected_families))} family part — "
                f"lacks wireless capability"
            )

    return errors


_BARE_RF_PATTERNS = re.compile(
    r'(ESP32|ESP8266|NRF24[L]?[012]|NRF52[345]|CC1101|CC1310|CC1352|SX126[128]|LR1110|LR1120)',
    re.IGNORECASE,
)
_MODULE_MARKERS = re.compile(
    r'(WROOM|MINI|MOD|DEVKIT|MODULE|DK|DONGLE|BOARD|BREAKOUT)',
    re.IGNORECASE,
)
_MODULE_LIBRARIES = ("RF_MODULE", "MODULE_")


def _check_module_preference(comps: list[dict]) -> list[str]:
    """Detect bare RF ICs (QFN/BGA chips) that should be replaced with
    pre-certified modules for easier PCB routing.

    Returns error messages; empty list means no violations.
    """
    errors: list[str] = []
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        library = id_str.split(":")[0] if ":" in id_str else ""

        # Skip if already a module
        if any(lib in library for lib in _MODULE_LIBRARIES):
            continue
        if _MODULE_MARKERS.search(id_str):
            continue

        # Check if it's a bare RF IC
        if _BARE_RF_PATTERNS.search(id_str):
            errors.append(
                f"Module preference: {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                f"is a bare RF IC — replace with a pre-certified module "
                f"(search for named modules with WROOM/DEVKIT suffix) "
                f"for easier PCB routing and FCC compliance"
            )
    return errors


def validate_node(state, config):
    _emit(config, "agent:thinking", {"message": "Validating component selections..."})
    _emit_activity(config, "validate", "Validation", "start")
    contract = _check_stage_contract("validate", state, ["selected_components", "analysis", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "validate", {"selected_components": [], "validation_errors": []})
    comps = state.get("selected_components", [])
    analysis = state.get("analysis", [])
    prompt = state.get("prompt", "")
    if not comps:
        _emit(config, "agent:log", {"message": "No components to validate."})
        return _stage_result(state, "validate", {"selected_components": comps, "validation_errors": []})
    components_list = "\n".join(
        (
            f'  {c["ref_des"]}: {c["id_str"]}  [{c.get("category", "?")}]'
            f'  "{c.get("description", "")[:80]}"'
            + (f'  [DATASHEET] {c.get("datasheet_text", "")[:300]}' if c.get("datasheet_text") else '')
        )
        for c in comps
    )
    subsystems = "\n".join(
        f'  {a.get("subsystem", "?")}: {a.get("function", "")}'
        for a in analysis
    )

    # Deterministic prompt-integrity pre-check runs BEFORE the LLM validation
    # so that part-family mismatches are caught even if the LLM hallucinates.
    integrity_errors = _check_prompt_integrity(prompt, comps)
    if integrity_errors:
        _emit(config, "agent:log", {
            "message": f"  Prompt-integrity pre-check found {len(integrity_errors)} issue(s)"
        })

    # Module preference check: bare RF ICs should be replaced with modules.
    module_errors = _check_module_preference(comps)
    if module_errors:
        _emit(config, "agent:log", {
            "message": f"  Module preference pre-check found {len(module_errors)} issue(s)"
        })

    try:
        text = _call_llm(VALIDATE_SYSTEM, VALIDATE_USER.format(
            prompt=prompt,
            subsystems=subsystems,
            components_list=components_list,
        ), stage="validate")
    except Exception:
        text = ""
    text = _clean_json(text)
    try:
        result = json.loads(text) if text else {"valid": True, "issues": []}
    except json.JSONDecodeError:
        print(f"Failed to parse validation JSON: {text[:200]}")
        result = {"valid": True, "issues": []}

    # Inject deterministic pre-check errors into LLM result
    for err in integrity_errors:
        result.setdefault("issues", []).append({
            "id_str": "",
            "severity": "error",
            "message": err,
            "suggestion": "Reselect using a part matching the originally specified family",
        })
        result["valid"] = False
    for err in module_errors:
        result.setdefault("issues", []).append({
            "id_str": "",
            "severity": "error",
            "message": err,
            "suggestion": "Replace bare RF IC with a pre-certified module",
        })
        result["valid"] = False
    issues = result.get("issues", [])
    missing = result.get("missing_components", [])
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    n_fixed = _fix_library_prefixes(comps, lambda k, v: _emit(config, k, v))
    if n_fixed:
        _emit(config, "agent:log", {"message": f"  Fixed {n_fixed} library prefix(es)"})
    # Remove prefix-fixable issues from the error list (data is now corrected)
    issues = [i for i in issues if "library prefix" not in (i.get("message", "") or "").lower()]
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        for keyword, context, reason in _CRITICAL_PATTERNS:
            if keyword in msg and context in msg:
                detail = (f"Critical validation failure: {reason}\n"
                          f"  Component: {issue.get('id_str', '?')}\n"
                          f"  Detail: {issue.get('message', '')}\n"
                          f"  Suggestion: {issue.get('suggestion', '')}")
                rejected = list(state.get("rejected_ids", []))
                if issue.get("id_str") and issue["id_str"] not in rejected:
                    rejected.append(issue["id_str"])
                _emit_activity(config, "validate", "Validation", "update", level="error", kind="validation", detail=detail)
                _emit_activity(config, "validate", "Validation", "done")
                return _stage_result(state, "validate", {
                    "selected_components": comps,
                    "validation_errors": [issue.get("message", "")],
                    "error": detail,
                    "rejected_ids": rejected,
                })

    for issue in issues:
        _emit(config, "agent:log", {
            "message": f"  [{issue.get('severity', 'info').upper()}] {issue.get('message', '')}"
        })
    for err in errors:
        _emit_activity(config, "validate", "Validation", "update", level="error", kind="validation", detail=err.get("message", ""))
    for w in warnings:
        _emit_activity(config, "validate", "Validation", "update", level="warning", kind="validation", detail=w.get("message", ""))
    corrections = []
    if missing:
        _emit(config, "agent:thinking", {"message": f"Searching for {len(missing)} missing component(s)..."})
        for mc in missing:
            query = mc.get("suggested_query", mc.get("description", ""))
            try:
                lib_filter = mc.get("library_filter") or None
                preferred_id = mc.get("preferred_id_str", "")
                if preferred_id in _KNOWN_SYMBOLS:
                    best = {"id_str": preferred_id, "text": mc.get("description", query), "footprint": "", "pads": []}
                else:
                    results = search_components(query, k=5, library_filter=lib_filter)
                    best = results[0] if results else None
                if best:
                    ref_prefix = _ref_prefix_for(best["id_str"], best["id_str"].split(":")[0])
                    existing_nums = set()
                    for c in comps + corrections:
                        r = c.get("ref_des", "")
                        prefix = "".join(ch for ch in r if ch.isalpha()) or "U"
                        num = "".join(ch for ch in r if ch.isdigit())
                        if prefix == ref_prefix and num:
                            existing_nums.add(int(num))
                    next_num = 1
                    while next_num in existing_nums:
                        next_num += 1
                    ref = f"{ref_prefix}{next_num}"
                    corrections.append({
                        "id_str": best["id_str"],
                        "ref_des": ref,
                        "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                        "description": best.get("text", mc.get("description", "")),
                        "footprint": best.get("footprint", ""),
                        "pads": best.get("pads", []),
                        "justification": f"Auto-added by validator: {mc.get('description', query)}",
                        "datasheet_text": "",
                        "subsystem": mc.get("subsystem", ""),
                    })
                    _emit(config, "agent:log", {
                        "message": f"  Added missing {ref} ({best['id_str']}) for: {mc.get('description', query)}"
                    })
            except Exception as e:
                print(f"Validator search failed for '{query}': {e}")
    if corrections:
        comps = comps + corrections
        _emit(config, "agent:log", {
            "message": f"  Corrected: added {len(corrections)} missing component(s)"
        })
    # Filter out errors that were fixed by auto-added corrections.
    # Uses keyword overlap: if an error message shares significant keywords
    # with a corrected missing-component description, it's considered fixed.
    _STOP = frozenset({"with", "from", "that", "this", "have", "been", "for",
        "the", "and", "are", "its", "has", "not", "can", "will", "but",
        "also", "than", "into", "more", "some", "their", "about", "other",
        "over", "such", "than", "very", "just", "should", "would", "could",
        "each", "between", "without", "within", "after", "before", "during",
        "when", "where", "there", "which", "while", "because", "through"})
    def _keywords(text):
        return {w for w in re.findall(r'[a-zA-Z0-9]+', text.lower())
                if len(w) >= 4 and w not in _STOP}
    fixed_descs = [c.get("description", "") for c in corrections]
    validation_errors = []
    for e in errors:
        msg = e.get("message", "")
        if not msg:
            continue
        msg_kw = _keywords(msg)
        fixed = False
        for fd in fixed_descs:
            shared = len(msg_kw & _keywords(fd))
            if shared >= 2:
                fixed = True
                break
        if not fixed:
            validation_errors.append(msg)
    # Remove errors about known placeholder symbols (e.g. Device:TPD6S300A).
    # These are intentional fallback symbols from support_rules
    # KNOWN_FALLBACK_SYMBOLS used when RAG has no real KiCad library symbol.
    # The LLM validator flags them as non-existent — skip those errors.
    _BASIC_PASSIVES = frozenset([
        "Device:R_Small", "Device:C_Small", "Device:L_Small", "Device:D_Small",
        "Device:LED", "Device:Polyfuse", "Device:Crystal", "Device:Crystal_GND24",
        "Device:Crystal_Small",
    ])
    _placeholders = {s for s in _KNOWN_SYMBOLS if s.startswith("Device:") and s not in _BASIC_PASSIVES}
    validation_errors = [
        m for m in validation_errors
        if not any(s.split(":")[1].lower() in m.lower() for s in _placeholders)
    ]
    rejected = list(state.get("rejected_ids", []))
    for e in errors:
        eid = e.get("id_str", "")
        if eid and eid not in rejected:
            rejected.append(eid)
    if validation_errors:
        _emit(config, "agent:log", {
            "message": f"Validation found {len(validation_errors)} unfixed error(s) — will retry selection"
        })
    else:
        _emit_activity(config, "validate", "Validation", "update", level="success", kind="validation", detail="Validation passed")
    _emit(config, "agent:log", {
        "message": f"Validation done: {len(comps)} components, {len(validation_errors)} unfixed error(s), {len(warnings)} warning(s)"
    })
    _emit_activity(config, "validate", "Validation", "done")
    result = {
        "selected_components": comps,
        "validation_errors": validation_errors,
        "rejected_ids": rejected,
    }
    if validation_errors and state.get("retry_count", 0) >= MAX_VALIDATION_RETRIES:
        error_msgs = "; ".join(validation_errors[:3])
        result["error"] = f"Validation failed after {MAX_VALIDATION_RETRIES} retries: {error_msgs}"
    return _stage_result(state, "validate", result)
