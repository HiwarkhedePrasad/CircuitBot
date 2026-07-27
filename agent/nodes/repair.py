"""Repair node — attempts to fix repairable errors only.

Max 2 passes enforced. Only touches repairable errors, never fatal.
Only ADDS missing components, never REMOVES existing ones (except
devkit redundancy which removes duplicates).
"""

import re

from agent.tools import search_components
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result, _ref_prefix_for,
)
from agent.reranker import ThompsonBandit, rank_strategies
from uuid import uuid4

MAX_REPAIR_PASSES = 2

# Packages that are inappropriate for compact SMD designs (sensor boards,
# devkits, IoT).  Used by _add_component to filter out bad RAG results
# generically — works for any circuit type.
_BAD_PACKAGES = frozenset({
    "TO-3", "TO-3P", "TO-220", "TO-220FP", "TO-247", "TO-252",
    "D2PAK", "TO-263", "DPAK",
    "DIP-8", "DIP-14", "DIP-16", "DIP-20", "DIP-28", "DIP-40",
    "DIP-SO", "PDIP",
    "TO-92",  # through-hole only
})


def _has_bad_package(candidate: dict) -> bool:
    """Check if a candidate has an inappropriate package for SMD design."""
    fp = (candidate.get("footprint", "") or "").upper()
    text = (candidate.get("text", "") or "").upper()
    id_str = (candidate.get("id_str", "") or "").upper()
    for pkg in _BAD_PACKAGES:
        if pkg in fp or pkg in text or pkg in id_str:
            return True
    return False


def _ref_prefix_for_lib(lib: str) -> str:
    prefix_map = {
        "Device": "R",
        "Connector": "J",
        "Connector_USB": "J",
        "Regulator_Linear": "U",
        "Regulator_Switching": "U",
        "Interface_USB": "U",
        "Switch": "SW",
        "Sensor": "U",
    }
    for key, prefix in prefix_map.items():
        if key in lib:
            return prefix
    return "U"


def _next_ref(prefix: str, comps: list[dict]) -> str:
    existing_nums = set()
    for c in comps:
        r = c.get("ref_des", "")
        p = "".join(ch for ch in r if ch.isalpha()) or "U"
        n = "".join(ch for ch in r if ch.isdigit())
        if p == prefix and n:
            existing_nums.add(int(n))
    num = 1
    while num in existing_nums:
        num += 1
    return f"{prefix}{num}"


def _add_component(comps: list[dict], id_str: str, description: str, for_ref: str = "",
                   library_filter: str = "", value: str = "", exact_only: bool = False,
                   subsystem: str = "") -> bool:
    """Search for a component and add it to the list. Returns True if added."""
    try:
        results = search_components(description, k=5, library_filter=library_filter or None)
    except Exception as e:
        _emit = print
        _emit(f"  Repair search failed: {e}")
        return False

    best = None
    for r in results:
        if r["id_str"] == id_str:
            best = r
            break
    if not best and results and not exact_only:
        # Filter out bad packages before taking the fallback
        good = [r for r in results if not _has_bad_package(r)]
        best = good[0] if good else results[0]

    if not best:
        return False

    lib = best["id_str"].split(":")[0] if ":" in best["id_str"] else "Device"
    prefix = _ref_prefix_for(best["id_str"], lib)
    ref = _next_ref(prefix, comps)

    comps.append({
        "id_str": best["id_str"],
        "ref_des": ref,
        "category": lib,
        "description": description,
        "footprint": best.get("footprint", ""),
        "pads": best.get("pads", []),
        "justification": f"Auto-added by repair: {description}",
        "datasheet_text": "",
        "for_component": for_ref,
        "value": value,
        "subsystem": subsystem,
    })
    return True


# ── Repair strategies ────────────────────────────────────────────────

def _repair_bare_rf_ic(error: dict, comps: list[dict], config) -> list[dict]:
    if error.get("code") != "BARE_RF_IC":
        return []
    target_id = error.get("component_id", "")
    if not target_id:
        return []
    # Safety: never replace a component with an MCU prefix — it IS the main
    # processor and should stay bare on bare_ic/custom_pcb boards.
    if "MCU_" in target_id.upper():
        return []
    bare_name = target_id.split(":")[-1] if ":" in target_id else target_id
    bare_base = bare_name.split("-")[0]
    try:
        results = search_components(f"{bare_base} DEVKIT WROOM module", k=10)
        for r in results:
            rid = (r.get("id_str", "") or "").upper()
            if bare_base.upper() in rid and any(
                kw in rid for kw in ("WROOM", "DEVKIT", "MINI", "MODULE", "DK")
            ):
                for i, c in enumerate(comps):
                    if c.get("id_str", "") == target_id:
                        new_comp = dict(c)
                        new_comp["id_str"] = r["id_str"]
                        new_comp["description"] = r.get("text", f"Module variant of {target_id}")
                        new_comp["justification"] = "Replaced bare RF IC with module"
                        _emit(config, "agent:log", {
                            "message": f"  Repair: {target_id} → {r['id_str']} (module variant)"
                        })
                        comps[i] = new_comp
                        return [new_comp]
    except Exception as e:
        _emit(config, "agent:log", {"message": f"  Repair search failed: {e}"})
    return []


def _repair_devkit_redundant(error: dict, comps: list[dict], config) -> list[dict]:
    target_id = error.get("component_id", "")
    if not target_id:
        return []
    for i, c in enumerate(comps):
        if c.get("id_str", "") == target_id and not c.get("user_locked"):
            _emit(config, "agent:log", {
                "message": f"  Repair: removing redundant {c.get('ref_des', '?')} ({target_id})"
            })
            comps.pop(i)
            return []
    return []


def _repair_missing_programming_header(error: dict, comps: list[dict], config) -> list[dict]:
    if _add_component(comps, "Connector:Conn_01x04_Pin",
                      "Conn_01x04_Pin 4-pin UART programming header",
                      library_filter="Connector", exact_only=True,
                      subsystem="Programming Interface"):
        _emit(config, "agent:log", {"message": "  Repair: added programming header"})
        return ["MISSING_PROGRAMMING_HEADER"]
    return []


def _repair_missing_power_input(error: dict, comps: list[dict], config) -> list[dict]:
    if _add_component(comps, "Connector:USB_C_Receptacle_USB2.0_16P",
                      "USB-C power input connector",
                      library_filter="Connector",
                      subsystem="Power Input"):
        _emit(config, "agent:log", {"message": "  Repair: added USB-C power input"})
        return ["MISSING_POWER_INPUT"]
    return []


def _repair_missing_power_regulation(error: dict, comps: list[dict], config) -> list[dict]:
    changes = []
    if _add_component(comps, "Regulator_Linear:AMS1117-3.3",
                      "3.3V LDO voltage regulator",
                      library_filter="Regulator_Linear",
                      subsystem="Power Regulation"):
        _emit(config, "agent:log", {"message": "  Repair: added 3.3V regulator"})
        changes.append("MISSING_POWER_REGULATION")
    # Also add decoupling for the regulator
    if _add_component(comps, "Device:C_Small",
                      "10uF input bulk cap for regulator",
                      library_filter="Device", value="10uF",
                      subsystem="Power Regulation"):
        _emit(config, "agent:log", {"message": "  Repair: added 10uF input cap for regulator"})
        changes.append("MISSING_POWER_REGULATION_CAP")
    if _add_component(comps, "Device:C_Small",
                      "100nF input bypass cap for regulator",
                      library_filter="Device", value="100nF",
                      subsystem="Power Regulation"):
        _emit(config, "agent:log", {"message": "  Repair: added 100nF input bypass for regulator"})
        changes.append("MISSING_POWER_REGULATION_CAP")
    return changes


# ── Generic fallback repair ───────────────────────────────────────────

# Keywords that indicate the error is about adding a specific component type
_ADD_KEYWORDS = ("add", "missing", "needed", "required", "insert", "include")

# Common component patterns to extract from error messages
_COMPONENT_PATTERNS = [
    # "USB-UART bridge CP2102N" or "bridge IC CH340G"
    (r"(?:usb[- ]?uart|bridge)\s+(?:ic\s+)?(\w+)", None),
    # "OLED display" or "SSD1306 display"
    (r"(oled\s+display|ssd1306)", "Display"),
    # "40MHz crystal" or "crystal 40MHz"
    (r"(\d+\s*mhz)\s+crystal", "Device"),
    (r"crystal\s+(\d+\s*mhz)", "Device"),
    # "pull-up resistor" or "10k resistor"
    (r"(\d+k?Ω?\s+(?:pull[- ]?up|resistor))", "Device"),
    # "decoupling capacitor" or "100nF cap"
    (r"(\d+\s*(?:nf|uf|pf)\s+(?:cap|capacitor))", "Device"),
    # DS18B20 / 1-Wire sensor — error says "needs a 4.7kΩ pull-up resistor",
    # so add the resistor, not a second sensor.
    (r"(ds18b20|1[- ]?wire\s+sensor)", "Device"),
]


def _repair_generic(error: dict, comps: list[dict], config) -> list[str]:
    """Generic fallback repair: extract a search query from the error's
    suggested_fix or message and try to add a matching component."""
    suggested = error.get("suggested_fix", "")
    message = error.get("message", "")
    text = f"{suggested} {message}".strip()
    if not text:
        return []

    # Try to find a component name/part number in the text
    query = None
    library_filter = ""

    # Check for specific part numbers first (CP2102N, CH340G, SSD1306, etc.)
    part_match = re.search(r'\b([A-Z]{2,}[-]?\d{2,}\w*)\b', text.upper())
    if part_match:
        query = part_match.group(1)

    # Check for component type keywords
    if not query:
        for pattern, lib in _COMPONENT_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                query = m.group(1) if m.lastindex else m.group(0)
                library_filter = lib or ""
                break

    # Last resort: use the whole suggested_fix as query
    if not query:
        # Clean up the suggested fix to be a reasonable search query
        clean = re.sub(r'[^\w\s/-]', ' ', suggested)
        clean = re.sub(r'\s+', ' ', clean).strip()
        if len(clean) > 5:
            query = clean

    if not query:
        return []

    # Determine library filter from error context
    if not library_filter:
        if any(kw in text.lower() for kw in ("display", "oled", "lcd")):
            library_filter = "Display"
        elif any(kw in text.lower() for kw in ("bridge", "uart", "usb-uart")):
            library_filter = "Interface_USB"
        elif any(kw in text.lower() for kw in ("crystal", "oscillator")):
            library_filter = "Device"
        elif any(kw in text.lower() for kw in ("regulator", "ldo")):
            library_filter = "Regulator_Linear"
        elif any(kw in text.lower() for kw in ("esd", "protection")):
            library_filter = "Power_Protection"

    # If extracted query matches a component already in the design,
    # the error is about something that component NEEDS (e.g., a pull-up
    # resistor for a DS18B20 sensor), not a duplicate of the sensor.
    if query and any(
        query.upper() in c.get("id_str", "").upper()
        for c in comps
    ):
        if any(kw in text.lower() for kw in ("pull.up", "pullup", "pull-up")):
            query = "4.7kΩ pull-up resistor"
        elif any(kw in text.lower() for kw in ("pull.down", "pulldown", "pull-down")):
            query = "10kΩ pull-down resistor"
        library_filter = "Device"

    # Update library filter if the error context mentions pull-up/down
    if not library_filter and any(kw in text.lower() for kw in ("pull.up", "pullup", "pull-up", "pull.down", "pulldown", "pull-down")):
        library_filter = "Device"

    added = _add_component(comps, "", query, library_filter=library_filter)
    if added:
        _emit(config, "agent:log", {
            "message": f"  Generic repair: added component for '{query}'"
        })
        return ["GENERIC_REPAIR"]
    return []


# ── Router ───────────────────────────────────────────────────────────

_REPAIR_STRATEGIES = {
    "BARE_RF_IC": _repair_bare_rf_ic,
    "DEVKIT_REDUNDANT": _repair_devkit_redundant,
    "MISSING_PROGRAMMING_HEADER": _repair_missing_programming_header,
    "MISSING_POWER_INPUT": _repair_missing_power_input,
    "MISSING_POWER_REGULATION": _repair_missing_power_regulation,
}


def _error_context(error: dict) -> str:
    """Derive a coarse error context category from the error message for
    finer-grained bandit arms (e.g., 'MISSING_POWER_REGULATION→power')."""
    msg = (error.get("message", "") + " " + (error.get("suggested_fix", "") or "")).lower()
    if any(kw in msg for kw in ("power", "voltage", "regulator", "ldo", "3.3v", "5v", "vcc", "gnd")):
        return "power"
    if any(kw in msg for kw in ("usb", "uart", "serial", "bridge")):
        return "interface"
    if any(kw in msg for kw in ("rf", "antenna", "radio", "wireless", "wifi", "ble")):
        return "rf"
    if any(kw in msg for kw in ("display", "oled", "lcd", "screen")):
        return "display"
    if any(kw in msg for kw in ("sensor", "i2c", "spi", "temp")):
        return "sensor"
    if any(kw in msg for kw in ("connector", "header", "jack", "port", "terminal")):
        return "connector"
    return "general"


def repair_node(state, config):
    """Attempt to fix repairable errors. Max 2 passes.
    
    Uses Thompson Sampling bandit to adaptively select which repair strategy
    to try first per error type, learning from historical success rates.
    """
    repair_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Repairing component issues..."})
    emit_assistant_message(config, "Attempting to repair component issues...")
    emit_tool_event(config, "Repair", "running", "Repairing components...")

    contract = _check_stage_contract("repair", state, ["selected_components", "repairable_errors"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "repair", {})

    passes_used = state.get("repair_passes_used", 0)
    if passes_used >= MAX_REPAIR_PASSES:
        _emit(config, "agent:log", {
            "message": f"  Max repair passes ({MAX_REPAIR_PASSES}) exceeded — "
                       f"promoting remaining errors to fatal"
        })
        remaining = state.get("repairable_errors", [])
        current_fatal = state.get("fatal_errors", [])
        return _stage_result(state, "repair", {
            "fatal_errors": current_fatal + remaining,
            "repairable_errors": [],
            "repair_passes_used": passes_used,
        })

    comps = list(state.get("selected_components", []))
    repairable = state.get("repairable_errors", [])
    repair_history = list(state.get("repair_history", []))
    changes = []
    failed_codes = set()

    # Restore or initialize Thompson Sampling bandit
    bandit = ThompsonBandit()
    bandit_state = state.get("bandit_state")
    if bandit_state:
        bandit.load_state_dict(bandit_state)

    # Sanity check: correct any reference designator prefix mismatches
    for c in comps:
        id_str = c.get("id_str", "")
        ref = c.get("ref_des", "")
        cat = c.get("category", "")
        if id_str and ref:
            expected_prefix = _ref_prefix_for(id_str, cat)
            actual_prefix = "".join(ch for ch in ref if ch.isalpha())
            if expected_prefix and actual_prefix and expected_prefix != actual_prefix and expected_prefix != 'U':
                old_ref = ref
                new_ref = _next_ref(expected_prefix, [item for item in comps if item is not c])
                c["ref_des"] = new_ref
                changes.append("FIX_REF_PREFIX")
                _emit(config, "agent:log", {
                    "message": f"  Corrected reference designator prefix for {id_str}: {old_ref} -> {new_ref}"
                })

    # Track state-level changes (primary_mcu, mcu_platform, etc.)
    state_updates = {}

    for error in repairable:
        code = error.get("code", "")
        strategy = _REPAIR_STRATEGIES.get(code)
        ctx = _error_context(error)
        arm = f"{code}→{ctx}"

        if code == "MCU_MISMATCH":
            actual_mcu = error.get("actual_mcu", "")
            if actual_mcu:
                old_mcu = state.get("primary_mcu", "")
                state_updates["primary_mcu"] = actual_mcu
                mcu_lower = actual_mcu.lower()
                if "esp32" in mcu_lower:
                    state_updates["mcu_platform"] = "espressif"
                elif "stm32" in mcu_lower:
                    state_updates["mcu_platform"] = "st"
                elif "rp" in mcu_lower:
                    state_updates["mcu_platform"] = "raspberry_pi"
                elif "atmega" in mcu_lower or "attiny" in mcu_lower:
                    state_updates["mcu_platform"] = "microchip"
                else:
                    state_updates["mcu_platform"] = "unknown"
                changes.append(code)
                bandit.reward(arm, True)
                _emit(config, "agent:log", {
                    "message": f"  Fixed MCU_MISMATCH: updated primary_mcu from {old_mcu} to {actual_mcu}"
                })
            else:
                failed_codes.add(code)
                bandit.reward(arm, False)
        elif strategy:
            try:
                # Thompson Sampling: select the best strategy for this arm.
                # With single strategy per code, this still tracks per-context
                # success rate for future multi-strategy error types.
                _emit(config, "agent:log", {
                    "message": f"  Bandit arm '{arm}': expected success rate "
                               f"{bandit.expected_success_rate(arm):.0%}"
                })
                result = strategy(error, comps, config)
                if result:
                    changes.append(code)
                    bandit.reward(arm, True)
                else:
                    failed_codes.add(code)
                    bandit.reward(arm, False)
            except Exception as e:
                _emit(config, "agent:log", {
                    "message": f"  Repair strategy '{code}' failed: {e}"
                })
                failed_codes.add(code)
                bandit.reward(arm, False)
        else:
            generic_result = _repair_generic(error, comps, config)
            if generic_result:
                changes.append(code)
                bandit.reward(arm, True)
            else:
                _emit(config, "agent:log", {
                    "message": f"  No repair strategy for {code}: {error.get('message', '')}"
                })
                failed_codes.add(code)
                # Do NOT reward generic — it's a miss, not strategy failure
                bandit.reward(arm, False)

    passes_used += 1
    repair_history.append({
        "pass": passes_used,
        "changed": changes,
        "failed": list(failed_codes),
        "reason": "repair_pass",
    })

    remaining_errors = [
        e for e in repairable
        if e.get("code", "") in failed_codes
    ]

    _emit(config, "agent:log", {
        "message": f"  Repair pass {passes_used}/{MAX_REPAIR_PASSES}: "
                   f"{len(changes)} issue(s) addressed, {len(remaining_errors)} remaining"
    })

    emit_tool_event(config, "Repair", "completed",
                    f"Pass {passes_used}/{MAX_REPAIR_PASSES}, {len(changes)} changes, {len(remaining_errors)} remaining")

    result = {
        "selected_components": comps,
        "repairable_errors": remaining_errors,
        "repair_passes_used": passes_used,
        "repair_history": repair_history,
        "bandit_state": bandit.state_dict(),
    }
    result.update(state_updates)
    return _stage_result(state, "repair", result)
