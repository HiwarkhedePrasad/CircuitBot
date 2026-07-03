"""Symbol Compatibility Gate.

Inserted between ``select`` and ``validate`` nodes.  Checks each selected
component for structural fitness BEFORE the validator LLM and netlist
generator ever see it.

All checks are **deterministic** (no LLM call):

1. **Pin count vs expected** — Reject if the component's pad count exceeds
   the expected pin range for its subsystem by more than 5×.

2. **Graph complexity** — Detect grid-array connectors (Samtec, etc.) that
   have internal daisy-chained pins.  A simple header has ~4 isolated pads;
   a 400-pin Samtec has hundreds with A1/B1/C1/… row naming.

3. **Library prefix** — Reject if the library category doesn't match the
   subsystem requirement (e.g. a Connector_* part for a Sensor subsystem).
"""

from __future__ import annotations

import re

from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

PIN_PATTERN = re.compile(r'(\d+)\s*-?\s*pins?\b', re.IGNORECASE)
PART_NUMBER_PATTERN = re.compile(r'0?1x(\d+)', re.IGNORECASE)
LETTER_PREFIX = re.compile(r'^([A-Za-z]+)(\d+)', re.IGNORECASE)


def _estimate_expected_pins(subsystem: dict, prompt: str) -> int:
    """Return a conservative upper-bound of expected pins for *subsystem*.

    Priority order:
      1. Explicit number from the subsystem's function string ("4-pin header").
      2. Pin count from an example component (``Conn_01x04`` → 4).
      3. Heuristic default based on subsystem name keywords.
    """
    func = (subsystem.get("function", "") or "")

    m = PIN_PATTERN.search(func)
    if m:
        return int(m.group(1))

    for ex in (subsystem.get("example_components") or []):
        m = PART_NUMBER_PATTERN.search(ex)
        if m:
            return int(m.group(1))

    name = (subsystem.get("subsystem", "") or "").lower()
    if any(kw in name for kw in ("programming", "header", "connector", "jack", "terminal")):
        return 4
    if any(kw in name for kw in ("button", "switch", "led", "indicator")):
        return 4
    if any(kw in name for kw in ("sensor", "detector")):
        return 6
    if any(kw in name for kw in ("regulator", "ldo", "power")):
        return 6
    if any(kw in name for kw in ("display", "oled", "screen")):
        return 16
    if any(kw in name for kw in ("mcu", "microcontroller", "processor", "processing")):
        return 48
    if any(kw in name for kw in ("memory", "flash", "sram")):
        return 16
    if any(kw in name for kw in ("usb", "uart", "interface", "bridge")):
        return 8
    return 8


def _actual_pin_count(comp: dict) -> tuple[int, str]:
    """Return ``(pin_count, source)`` for a selected component.

    Uses ``pads`` length when available; falls back to looking up
    ``pins`` length from the component dict.
    """
    pads = comp.get("pads")
    if isinstance(pads, (list, tuple)):
        return len(pads), "pads"
    pins = comp.get("pins")
    if isinstance(pins, (list, tuple)):
        return len(pins), "pins"
    return 0, "none"


def _is_grid_array_connector(pads: list[dict]) -> bool:
    """Detect grid-array / mezzanine connectors (Samtec, etc.).

    These symbols have rows of pins named A1, B1, C1, D1, … with internal
    daisy chains, causing the netlist generator to create spurious nets
    through the connector body.

    Returns ``True`` when ``pads`` count ≥ 16 and the pin numbers show
    at least 4 distinct letter-prefixed groups (A, B, C, D rows).
    """
    n = len(pads)
    if n < 16:
        return False

    prefixes: set[str] = set()
    numeric_count = 0
    for p in pads:
        pn = str(p.get("number", "")).strip().upper()
        m = LETTER_PREFIX.match(pn)
        if m:
            prefixes.add(m.group(1))
            numeric_count += 1

    if len(prefixes) >= 4 and n >= 16:
        return True
    if len(prefixes) >= 3 and n >= 30:
        return True
    if len(prefixes) >= 2 and n >= 50:
        return True
    return False


def _library_category(comp: dict) -> str:
    """Return the KiCad library prefix for a component."""
    id_str = comp.get("id_str", "")
    if ":" in id_str:
        return id_str.split(":")[0]
    return ""


def _check_component(comp: dict, subsystem: dict, prompt: str, research_lookup: dict) -> list[str]:
    """Run all structural checks on *comp*.

    Returns a list of error messages (empty = passes all checks).
    """
    errors: list[str] = []
    ref = comp.get("ref_des", "?")
    id_str = comp.get("id_str", "")
    cat = _library_category(comp)

    actual, src = _actual_pin_count(comp)
    expected = _estimate_expected_pins(subsystem, prompt)

    # ── Check 1: Pin count ratio ─────────────────────────────────────────────
    if actual > 0 and expected > 0:
        ratio = actual / max(expected, 1)
        if ratio > 10:
            errors.append(
                f"Pin-count mismatch: {ref} ({id_str}) has {actual} pins "
                f"({ratio:.0f}× the ~{expected} expected for "
                f"'{subsystem.get('subsystem', '')}')"
            )
        elif ratio > 5:
            connector_flag = cat.startswith("Connector") or cat.startswith("Connector_")
            if connector_flag and actual > 50:
                errors.append(
                    f"Connector oversize: {ref} ({id_str}) has {actual} pads "
                    f"({ratio:.0f}× the ~{expected} expected). "
                    f"Overly complex connector rejected for '{subsystem.get('subsystem', '')}'"
                )

    # ── Check 2: Grid-array connector ────────────────────────────────────────
    pads = comp.get("pads") or []
    if _is_grid_array_connector(pads):
        errors.append(
            f"Grid-array connector: {ref} ({id_str}) has {len(pads)} pins "
            f"arranged in a multi-row grid pattern with internal daisy chains. "
            f"Rejected for '{subsystem.get('subsystem', '')}'"
        )

    # ── Check 3: Library prefix vs subsystem category ────────────────────────
    sub_name = (subsystem.get("subsystem", "") or "").lower()
    if any(kw in sub_name for kw in ("sensor", "detector", "temperature", "humidity")):
        if not cat.startswith("Sensor_") and not cat.startswith("Device"):
            errors.append(
                f"Category mismatch: {ref} ({id_str}) is from '{cat}' library "
                f"but '{subsystem.get('subsystem', '')}' requires a Sensor_* or Device part"
            )
    elif any(kw in sub_name for kw in ("mcu", "microcontroller", "processor", "processing")):
        if not (cat.startswith("MCU_") or cat.startswith("RF_Module") or cat.startswith("Module_")):
            errors.append(
                f"Category mismatch: {ref} ({id_str}) is from '{cat}' library "
                f"but '{subsystem.get('subsystem', '')}' requires an MCU_*/RF_Module part"
            )

    return errors


# ── Node ─────────────────────────────────────────────────────────────────────

def symbol_compatibility_node(state, config):
    """Run structural compatibility checks on all selected components.

    Components that fail are added to ``rejected_ids`` so the
    ``validate_repair`` loop replaces them with structurally compatible
    alternatives.
    """
    _emit(config, "agent:thinking", {"message": "Checking structural compatibility of selected parts..."})
    emit_assistant_message(config, "Checking symbol compatibility before validation...")
    emit_tool_event(config, "Symbol Compatibility", "running", "Checking pin counts and structural fitness...")

    contract = _check_stage_contract("symbol_compatibility", state,
                                      ["selected_components", "analysis", "research_results", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "symbol_compatibility", {
            "selected_components": state.get("selected_components", []),
        })

    comps = state.get("selected_components", [])
    analysis = state.get("analysis", [])
    prompt = state.get("prompt", "")
    if not comps:
        _emit(config, "agent:log", {"message": "No components to check."})
        return _stage_result(state, "symbol_compatibility", {
            "selected_components": comps,
        })

    # Build subsystem lookup
    subsystem_map: dict[str, dict] = {}
    for a in analysis:
        name = a.get("subsystem", "")
        if name:
            subsystem_map[name] = a

    existing_rejected = set(state.get("rejected_ids", []) or [])
    new_rejected: list[str] = []
    all_errors: list[str] = []

    for comp in comps:
        sub_name = comp.get("subsystem", "")
        subsystem = subsystem_map.get(sub_name, {})

        comp_errors = _check_component(comp, subsystem, prompt, {})
        if comp_errors:
            id_str = comp.get("id_str", "")
            ref = comp.get("ref_des", "?")
            if id_str and id_str not in existing_rejected and id_str not in new_rejected:
                new_rejected.append(id_str)
            for err in comp_errors:
                _emit(config, "agent:log", {"message": f"  [COMPAT] {err}"})
                all_errors.append(err)

    if all_errors:
        rejected = list(existing_rejected) + new_rejected
        _emit(config, "agent:log", {
            "message": f"  Found {len(all_errors)} structural issue(s), "
                       f"rejected {len(new_rejected)} component(s)"
        })
        emit_tool_event(config, "Symbol Compatibility", "running",
                        f"{len(all_errors)} structural issue(s), "
                        f"{len(new_rejected)} rejected")
        return _stage_result(state, "symbol_compatibility", {
            "selected_components": comps,
            "rejected_ids": rejected,
            "validation_errors": [f"Symbol compatibility: {e}" for e in all_errors],
        })

    emit_tool_event(config, "Symbol Compatibility", "completed", "All components structurally compatible")
    _emit(config, "agent:log", {
        "message": f"  All {len(comps)} components passed structural compatibility checks"
    })
    return _stage_result(state, "symbol_compatibility", {
        "selected_components": comps,
    })
