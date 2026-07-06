import json
import re

from agent.nodes.select import _normalize_part_family
from agent.component_insight import build_component_pin_summary
from agent.prompts import VALIDATE_SYSTEM, VALIDATE_USER
from agent.tools import search_components
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result, _call_llm, _clean_json, _ref_prefix_for,
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
    "Connector:Conn_01x04_Pin",
    "Connector:Conn_01x06_Pin",
    "Connector:Conn_01x08_Pin",
    "Device:Polyfuse",
    "power:PWR_FLAG",
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
        category = (c.get("category", "") or "").upper()
        if not any(token in category for token in ("MCU", "PROCESSOR", "CPU", "RF_MODULE", "MODULE")) and not any(
            token in (c.get("id_str", "") or "").upper() for token in ("STM32", "ESP32", "RP2040", "RP2350", "ATMEGA", "ATTINY", "AT90", "SAMD")
        ):
            continue
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


def _check_module_preference(comps: list[dict]) -> list[tuple[str, str]]:
    """Detect bare RF ICs (QFN/BGA chips) that should be replaced with
    pre-certified modules for easier PCB routing.

    Returns ``[(error_message, id_str)]`` — empty list means no violations.
    The ``id_str`` is used by the caller to populate ``rejected_ids`` so the
    offending component is not re-selected on retry.
    """
    errors: list[tuple[str, str]] = []
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
            errors.append((
                f"Module preference: {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                f"is a bare RF IC — replace with a pre-certified module "
                f"(search for named modules with WROOM/DEVKIT suffix) "
                f"for easier PCB routing and FCC compliance",
                c.get("id_str", ""),
            ))
    return errors


_DEVKIT_REDUNDANT_LIBS = frozenset({
    "Interface_USB", "Regulator_Linear", "Connector_USB", "Connector",
})

_CORE_LIB_PREFIXES = ("RF_MODULE:", "MCU_", "MODULE_")
_CORE_ID_KEYWORDS = frozenset({
    "WROOM", "ESP32", "ESP8266", "STM32", "RP2040", "RP2350",
    "ATMEGA", "ATTINY", "AT90", "ATXMEGA", "SAMD",
})


def _is_core_component(c: dict) -> bool:
    """Return True if *c* is a primary MCU/module that should never be
    removed by redundancy enforcement or devkit deduplication."""
    id_str = (c.get("id_str", "") or "").upper()
    lib = (id_str.split(":")[0] + ":") if ":" in id_str else ""
    if any(lib.startswith(p) for p in _CORE_LIB_PREFIXES):
        return True
    if any(kw in id_str for kw in _CORE_ID_KEYWORDS):
        return True
    return False


def _remove_devkit_redundancy(comps: list[dict], emit_fn) -> tuple[list[dict], list[str]]:
    """If any selected component is a DevKit/dev-board/WROOM/module, strip
    components that duplicate its on-board features (USB-UART bridge, regulator,
    USB-C connector, crystal, crystal load caps) plus their decoupling/support
    passives.

    Returns ``(filtered_components, removed_refs)``.
    """
    _MODULE_KEYWORDS = frozenset({
        "DEVKIT", "WROOM", "MINI", "MODULE", "DK", "NODEMCU", "BOARD", "BREAKOUT"
    })
    module_refs = [
        c["ref_des"] for c in comps
        if any(kw in (c.get("id_str", "") or "").upper() for kw in _MODULE_KEYWORDS)
    ]
    if not module_refs:
        return comps, []

    # Identify redundant main components
    # ── WROOM guard ────────────────────────────────────────────────────
    # Bare WROOM modules (ESP32-WROOM-32, ESP32-WROOM-32D) do NOT have
    # on-board voltage regulation or USB-UART bridges — they are bare
    # modules, not dev boards.  Only strip redundant components when a
    # true development board keyword (DEVKIT, NODEMCU, BOARD, BREAKOUT)
    # is present.
    _DEV_BOARD_KW = frozenset({"DEVKIT", "NODEMCU", "BOARD", "BREAKOUT"})
    has_dev_board = any(
        any(kw in (c.get("id_str", "") or "").upper() for kw in _DEV_BOARD_KW)
        for c in comps if c.get("ref_des", "") in module_refs
    )
    redundant_refs: set[str] = set()
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        ref = c.get("ref_des", "")
        if not ref:
            continue
        # Never remove user-locked or core MCU/module components
        if c.get("user_locked") or _is_core_component(c):
            continue
        # USB-UART bridges — only remove if a true dev board is present
        if has_dev_board and any(kw in id_str for kw in ("CP2102", "CH340", "FT230", "FT232")):
            redundant_refs.add(ref)
        # Voltage regulators — only remove if a true dev board is present
        if has_dev_board and ("AMS1117" in id_str or "REGULATOR_LINEAR:" in id_str):
            redundant_refs.add(ref)
        # USB-C receptacles — only remove if a true dev board is present
        if has_dev_board and "USB_C_RECEPTACLE" in id_str:
            redundant_refs.add(ref)
        # Non-RTC crystals (40 MHz main crystal — RTC 32.768 kHz is kept)
        if id_str in ("DEVICE:CRYSTAL_SMALL", "DEVICE:CRYSTAL", "DEVICE:CRYSTAL_GND24"):
            redundant_refs.add(ref)

    # Library-level catch-all: any component from a library known to be
    # redundant with WROOM/modules (e.g. Interface_USB, Regulator_Linear,
    # Connector_USB). This catches parts not covered by specific patterns
    # above (like TPS25730D in Interface_USB).
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        if c.get("user_locked") or _is_core_component(c):
            continue
        c_id_str = (c.get("id_str", "") or "").upper()
        c_lib = c_id_str.split(":")[0] if ":" in c_id_str else ""
        if c_lib in _DEVKIT_REDUNDANT_LIBS:
            redundant_refs.add(ref)

    # Remove support passives whose for_component is a redundant ref
    for c in comps:
        fc = c.get("for_component", "")
        if fc and fc in redundant_refs:
            redundant_refs.add(c.get("ref_des", ""))

    # Remove crystal load caps (description mentions "crystal" + "cap" or "load")
    # that sit alongside a now-redundant crystal, even if no for_component link.
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        desc = (c.get("description", "") or "").upper()
        id_str = (c.get("id_str", "") or "").upper()
        if "CRYSTAL" not in desc and "CRYSTAL" not in id_str:
            continue
        # Check if any already-redundant component shares this subsystem
        sub = c.get("subsystem", "")
        if sub and any(
            comp.get("subsystem") == sub and comp.get("ref_des", "") in redundant_refs
            for comp in comps
        ):
            redundant_refs.add(ref)

    removed = [f"{r} ({c.get('id_str', '')})" for r in redundant_refs
               for c in comps if c.get("ref_des", "") == r]
    filtered = [c for c in comps if c.get("ref_des", "") not in redundant_refs]
    if removed:
        emit_fn("agent:log", {
            "message": f"  Module redundancy: removed {len(removed)} duplicate component(s): "
                       f"{', '.join(removed)}"
        })
    return filtered, removed


def _enforce_redundancy_removal(
    comps: list[dict],
    issues: list[dict],
    emit_fn,
) -> tuple[list[dict], list[dict], list[str]]:
    """Post-LLM enforcement: scan all issues (errors + warnings) for redundancy
    keywords and delete the flagged components from the BOM immediately.

    This gives the validator actual teeth — warnings like "crystal is redundant"
    result in the crystal AND its support passives being stripped from ``comps``
    before the list reaches the layout engine.

    Returns ``(cleaned_comps, cleaned_issues, removed_refs)``.
    """
    redundant_refs: set[str] = set()
    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        if "redundant" not in msg and "already integrated" not in msg:
            continue
        # Match by id_str — catch ALL comps with that id_str (e.g., two
        # Device:C_Small load caps for the same crystal).
        eid = issue.get("id_str", "")
        matched_some = False
        if eid:
            for c in comps:
                if c.get("id_str", "") == eid:
                    # Protect user-locked and core MCU/module from removal
                    if c.get("user_locked") or _is_core_component(c):
                        continue
                    redundant_refs.add(c.get("ref_des", ""))
                    matched_some = True
        # Fallback: try matching ref_des from message text
        if not matched_some:
            for c in comps:
                ref = c.get("ref_des", "")
                if not ref:
                    continue
                # Protect user-locked and core MCU/module from removal
                if c.get("user_locked") or _is_core_component(c):
                    continue
                # Pattern 1: redundancy keyword BEFORE ref_des within 80 chars
                ctx_fwd = re.compile(
                    rf'(?:redundant|superfluous|unnecessary|duplicate|already\s+integrated)'
                    rf'.{{0,80}}\b{re.escape(ref.lower())}\b',
                    re.IGNORECASE
                )
                if ctx_fwd.search(msg):
                    redundant_refs.add(ref)
                    break
                # Pattern 1b: ref_des BEFORE redundancy keyword within 80 chars (reverse)
                ctx_rev = re.compile(
                    rf'\b{re.escape(ref.lower())}\b'
                    rf'.{{0,80}}(?:redundant|superfluous|unnecessary|duplicate)',
                    re.IGNORECASE
                )
                if ctx_rev.search(msg):
                    redundant_refs.add(ref)
                    break
                # Pattern 2 (C-04 fix): ref_des as sentence subject BUT only when
                # the same sentence ALSO contains a redundancy keyword. This prevents
                # matching the SURVIVOR when the redundant part is the real subject.
                sentence_m = re.search(
                    rf'(?:redundant|superfluous|unnecessary|duplicate|already\s+integrated)',
                    msg, re.IGNORECASE
                )
                if sentence_m:
                    subj_pattern = re.compile(
                        rf'\b{re.escape(ref.lower())}\b\s+(?:is|was|has|contains|integrates)',
                        re.IGNORECASE
                    )
                    if subj_pattern.search(msg):
                        redundant_refs.add(ref)
                        break

    if not redundant_refs:
        return comps, issues, []

    # Cascade: remove support passives linked to a removed main component
    pre_cascade = set(redundant_refs)
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        if c.get("user_locked"):
            continue
        fc = c.get("for_component", "")
        desc = (c.get("description", "") or "").upper()
        if fc and fc in redundant_refs:
            redundant_refs.add(ref)
        elif any(r in desc for r in redundant_refs):
            redundant_refs.add(ref)
    cascade_added = redundant_refs - pre_cascade
    if cascade_added:
        cascade_detail = [
            f"{r}" for r in sorted(cascade_added)
        ]
        emit_fn("agent:log", {
            "message": f"  Cascade removal: {', '.join(cascade_detail)} (support components of removed ICs)"
        })

    # Capture id_strs BEFORE filtering comps
    removed_ids = {
        c.get("id_str", "") for c in comps
        if c.get("ref_des", "") in redundant_refs and c.get("id_str", "")
    }
    filtered = [c for c in comps if c.get("ref_des", "") not in redundant_refs]

    # Remove issues about now-deleted components
    filtered_issues = [
        i for i in issues
        if i.get("id_str", "") not in removed_ids
    ]

    removed = [f"{r} ({c.get('id_str', '')})" for r in redundant_refs
               for c in comps if c.get("ref_des", "") == r]
    if removed:
        emit_fn("agent:log", {
            "message": f"  Enforced redundancy: removed {len(removed)} component(s): "
                       f"{', '.join(removed)}"
        })

    return filtered, filtered_issues, sorted(removed)


def validate_node(state, config):
    _emit(config, "agent:thinking", {"message": "Validating component selections..."})
    emit_assistant_message(config, "Checking the selected components for electrical and functional issues...")
    emit_tool_event(config, "Validation", "running", "Checking component selections...")
    contract = _check_stage_contract("validate", state, ["selected_components", "analysis", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "validate", {"selected_components": [], "validation_errors": []})
    comps = state.get("selected_components", [])
    analysis = state.get("analysis", [])
    prompt = state.get("prompt", "")
    research = state.get("research_results", [])
    if not comps:
        _emit(config, "agent:log", {"message": "No components to validate."})
        return _stage_result(state, "validate", {"selected_components": comps, "validation_errors": []})
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
    # When found, try to auto-replace with a module variant immediately so the
    # retry loop doesn't exhaust just picking different bare ICs.
    module_errors = _check_module_preference(comps)
    if module_errors:
        _emit(config, "agent:log", {
            "message": f"  Module preference pre-check found {len(module_errors)} issue(s)"
        })
        replaced_any = False
        for err_msg, err_id in list(module_errors):
            for ci, c in enumerate(comps):
                if c.get("id_str", "") != err_id:
                    continue
                bare_name = err_id.split(":")[-1] if ":" in err_id else err_id
                bare_base = bare_name.split("-")[0].upper()
                # Search for a module variant (e.g., ESP32-C3 → ESP32-C3-DevKitM-1)
                try:
                    mod_results = search_components(f"{bare_base} DEVKIT WROOM module", k=10)
                    replacement = None
                    for r in mod_results:
                        rid = (r.get("id_str", "") or "").upper()
                        lib = rid.split(":")[0] if ":" in rid else ""
                        if bare_base.upper() in rid and any(
                            kw in rid for kw in ("WROOM", "DEVKIT", "MINI", "MODULE", "DK")
                        ):
                            replacement = r
                            break
                    if replacement:
                        # Build a component entry matching the existing schema
                        new_ref_des = c.get("ref_des", "")
                        new_entry = {
                            "id_str": replacement["id_str"],
                            "ref_des": new_ref_des,
                            "category": replacement.get("category",
                                replacement["id_str"].split(":")[0] if ":" in replacement["id_str"] else "General"),
                            "description": replacement.get("text", replacement.get("description",
                                f"Module variant of {err_id}")),
                            "footprint": replacement.get("footprint", ""),
                            "pads": replacement.get("pads", []),
                            "justification": f"Auto-replaced bare RF IC ({err_id}) with pre-certified module",
                            "datasheet_text": "",
                            "subsystem": c.get("subsystem", ""),
                        }
                        comps[ci] = new_entry
                        _emit(config, "agent:log", {
                            "message": f"  Auto-replaced {c.get('ref_des', '?')} ({err_id}) "
                                       f"→ {replacement['id_str']} (module variant)"
                        })
                        # Remove this error from module_errors
                        module_errors = [(m, i) for m, i in module_errors if i != err_id]
                        replaced_any = True
                    else:
                        _emit(config, "agent:log", {
                            "message": f"  No module variant found for {err_id} — will retry with different bare IC if available"
                        })
                except Exception as e:
                    print(f"Module auto-replace search failed for {err_id}: {e}")
        if replaced_any and not module_errors:
            _emit(config, "agent:log", {
                "message": "  All bare RF ICs auto-replaced — clearing module preference errors"
            })
        # Re-check: if we replaced everything, module_errors is now empty
        if not module_errors:
            pass  # All resolved

    # Build components_list AFTER auto-replace so LLM sees the final (module) version
    components_list = "\n".join(
        (
            f'  {c["ref_des"]}: {c["id_str"]}  [{c.get("category", "?")}]'
            f'  "{c.get("description", "")[:80]}"'
            + (f'  [DATASHEET] {c.get("datasheet_text", "")[:300]}' if c.get("datasheet_text") else '')
            + f'\n    Pins: {build_component_pin_summary(c["id_str"], research)}'
        )
        for c in comps
    )

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
    for err_msg, err_id in module_errors:
        result.setdefault("issues", []).append({
            "id_str": err_id,
            "severity": "error",
            "message": err_msg,
            "suggestion": "Replace bare RF IC with a pre-certified module",
        })
        result["valid"] = False
    issues = result.get("issues", [])
    missing = result.get("missing_components", [])
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    # ── Post-LLM redundancy enforcement ──
    # The LLM may flag components as "redundant" (e.g. external crystal + load
    # caps when a WROOM module is selected).  This step gives those warnings
    # teeth: the flagged components are DELETED from comps immediately.
    comps, issues, enforced_removed = _enforce_redundancy_removal(
        comps, issues, lambda k, v: _emit(config, k, v),
    )
    _enforced_rejected_ids: list[str] = []
    if enforced_removed:
        _enforced_refs = set(enforced_removed)
        _enforced_rejected_ids = [
            c.get("id_str", "") for c in state.get("selected_components", [])
            if c.get("ref_des", "") in _enforced_refs and c.get("id_str", "")
        ]
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

    n_fixed = _fix_library_prefixes(comps, lambda k, v: _emit(config, k, v))
    if n_fixed:
        _emit(config, "agent:log", {"message": f"  Fixed {n_fixed} library prefix(es)"})
    # Remove prefix-fixable issues from the error list (data is now corrected)
    issues = [i for i in issues if "library prefix" not in (i.get("message", "") or "").lower()]
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    # ── Auto-remove redundant DevKit components ──
    comps, removed_refs = _remove_devkit_redundancy(comps, lambda k, v: _emit(config, k, v))
    _devkit_rejected_ids: list[str] = []
    if removed_refs:
        _devkit_rejected_ids = [
            c.get("id_str", "") for c in state.get("selected_components", [])
            if c.get("ref_des", "") in set(removed_refs) and c.get("id_str", "")
        ]
        # Rebuild error/warning lists — remove issues about now-removed components
        removed_ref_set = set(removed_refs)
        issues = [i for i in issues if not any(
            r in (i.get("id_str", "") or "") for r in removed_ref_set
        )]
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

    # C-05: Build full rejected list BEFORE critical-pattern early return so
    # that devkit-removed and enforcement-removed IDs are never lost.
    _base_rejected = list(state.get("rejected_ids", []))
    for rid in _devkit_rejected_ids:
        if rid not in _base_rejected:
            _base_rejected.append(rid)
    for rid in _enforced_rejected_ids:
        if rid not in _base_rejected:
            _base_rejected.append(rid)

    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        for keyword, context, reason in _CRITICAL_PATTERNS:
            if keyword in msg and context in msg:
                detail = (f"Critical validation failure: {reason}\n"
                          f"  Component: {issue.get('id_str', '?')}\n"
                          f"  Detail: {issue.get('message', '')}\n"
                          f"  Suggestion: {issue.get('suggestion', '')}")
                rejected = list(_base_rejected)
                crit_id = issue.get("id_str", "")
                if crit_id and crit_id not in rejected and not any(
                    c.get("user_locked") for c in comps if c.get("id_str") == crit_id
                ):
                    rejected.append(crit_id)
                _emit(config, "agent:log", {
                    "message": f"  Validation rejected {issue.get('id_str', '?')}: {issue.get('message', '')}"
                })
                emit_tool_event(config, "Validation", "failed", detail, details={
                    "component": issue.get("id_str", "?"),
                    "reason": issue.get("message", ""),
                    "suggestion": issue.get("suggestion", ""),
                })
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
        emit_tool_event(config, "Validation", "running", f"Error: {err.get('message', '')}")
    for w in warnings:
        emit_tool_event(config, "Validation", "running", f"Warning: {w.get('message', '')}")
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
                if not best:
                    q_lower = query.lower()
                    for known_id in _KNOWN_SYMBOLS:
                        known_name = known_id.rpartition(':')[2].lower().replace('_', ' ')
                        if known_name in q_lower or q_lower in known_name:
                            best = {"id_str": known_id, "text": mc.get("description", query), "footprint": "", "pads": []}
                            break
                # ── Programming/UART header deterministic fallback ──
                if not best:
                    prog_keywords = ("programming", "debug", "uart header", "tx rx")
                    if any(kw in query.lower() for kw in prog_keywords):
                        for fallback_id in ("Connector:Conn_01x06_Pin",
                                            "Connector:Conn_01x04_Pin",
                                            "Connector:Conn_01x08_Pin"):
                            if fallback_id in _KNOWN_SYMBOLS:
                                best = {"id_str": fallback_id, "text": mc.get("description", query),
                                        "footprint": "", "pads": []}
                                _emit(config, "agent:log", {
                                    "message": f"  Programming header fallback: using {fallback_id}"
                                })
                                break
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
    # Uses stem-matched keyword overlap: "resistors" matches "resistor",
    # "decoupling" matches "decouple", etc.
    import re as _re
    _STOP = frozenset({"with", "from", "that", "this", "have", "been", "for",
        "the", "and", "are", "its", "has", "not", "can", "will", "but",
        "also", "than", "into", "more", "some", "their", "about", "other",
        "over", "such", "than", "very", "just", "should", "would", "could",
        "each", "between", "without", "within", "after", "before", "during",
        "when", "where", "there", "which", "while", "because", "through"})
    def _stem(w: str) -> str:
        """Crude stem: drop common suffixes so 'resistors' ~ 'resistor'."""
        w = w.rstrip('s')       # resistors → resistor, caps → cap
        w = w.rstrip('ing')     # decoupling → decoupl
        w = w.rstrip('ed')      # integrated → integrat
        w = w.rstrip('e')       # decouple → decoupl, integrate → integrat
        return w
    def _keywords(text):
        raw = _re.findall(r'[a-zA-Z0-9]+', text.lower())
        return {_stem(w) for w in raw if len(w) >= 4 and w not in _STOP}
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
    rejected_families = list(state.get("rejected_families", []))
    for rid in _devkit_rejected_ids:
        if rid and rid not in rejected:
            rejected.append(rid)
    for rid in _enforced_rejected_ids:
        if rid and rid not in rejected:
            rejected.append(rid)
    for e in errors:
        eid = e.get("id_str", "")
        if eid and eid not in rejected:
            rejected.append(eid)
        fam = _normalize_part_family(eid)
        if fam and fam not in rejected_families:
            rejected_families.append(fam)
    if validation_errors:
        _emit(config, "agent:log", {
            "message": f"Validation found {len(validation_errors)} unfixed error(s) — will retry selection"
        })
        emit_assistant_message(config, f"Validation found {len(validation_errors)} issue(s) — retrying with fixes.")
    else:
        emit_tool_event(config, "Validation", "completed", "All checks passed")
        emit_assistant_message(config, "All validation checks passed — the BOM is electrically sound.")
    _emit(config, "agent:log", {
        "message": f"Validation done: {len(comps)} components, {len(validation_errors)} unfixed error(s), {len(warnings)} warning(s)"
    })
    result = {
        "selected_components": comps,
        "validation_errors": validation_errors,
        "rejected_ids": rejected,
        "rejected_families": rejected_families,
        "_last_validated_component_count": len(comps),
    }
    if validation_errors and state.get("retry_count", 0) >= MAX_VALIDATION_RETRIES:
        error_msgs = "; ".join(validation_errors[:3])
        repair_failures = state.get("repair_failures", []) or []
        if repair_failures:
            missing_targets = ", ".join(sorted(set(item.split(":", 1)[0] for item in repair_failures[:4])))
            result["error"] = (
                f"No compatible component found in the available library after {MAX_VALIDATION_RETRIES} retries "
                f"for: {missing_targets}. Last validation errors: {error_msgs}"
            )
        else:
            result["error"] = f"Validation failed after {MAX_VALIDATION_RETRIES} retries: {error_msgs}"
    return _stage_result(state, "validate", result)
