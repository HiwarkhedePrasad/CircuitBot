import re

from agent.nodes.select import (
    _expected_buckets,
    _candidate_buckets,
    _normalize_part_family,
)
from agent.tools import search_components
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _stage_result,
    _is_passive,
)


def _ref_prefix(category: str, id_str: str = '') -> str:
    text = f"{category} {id_str}".upper()
    rules = [
        ('CONNECTOR',  'J'), ('USB',  'J'), ('JACK',  'J'),
        ('FUSE',       'F'), ('POLYFUSE', 'F'),
        ('CAPACITOR',  'C'), ('C_SMALL', 'C'), ('C_POLARIZED', 'C'),
        ('RESISTOR',   'R'), ('R_SMALL', 'R'),
        ('INDUCTOR',   'L'),
        ('DIODE',      'D'), ('LED', 'D'), ('ZENER', 'D'),
        ('TRANSISTOR', 'Q'), ('MOSFET', 'Q'), ('BJT', 'Q'),
        ('REGULATOR',  'U'), ('LDO', 'U'), ('SENSOR', 'U'), ('DISPLAY', 'U'),
        ('MCU',        'U'), ('PROCESSOR', 'U'), ('CPU', 'U'),
        ('ESP32',      'U'), ('STM32', 'U'), ('FPGA', 'U'), ('MEMORY', 'U'),
        ('DRIVER',     'U'), ('AMS', 'U'), ('DS18', 'U'), ('ATMEGA', 'U'),
        ('OLED',       'U'), ('SSD', 'U'), ('ESD', 'U'), ('TPD', 'U'),
        ('SWITCH',     'SW'), ('BUTTON', 'SW'), ('TACTILE', 'SW'),
        ('SPEAKER',    'LS'), ('BUZZER', 'LS'),
        ('RELAY',      'K'),
        ('MOTOR',      'M'), ('FAN', 'M'),
        ('BATTERY',    'BT'),
        ('CRYSTAL',    'Y'), ('OSCILLATOR', 'Y'), ('RESONATOR', 'Y'),
        ('PWR_FLAG',   '#FLG'),
    ]
    for keyword, prefix in rules:
        if keyword in text:
            return prefix
    return 'U'


_DEVKIT_REDUNDANT_LIBS = frozenset({
    "Interface_USB", "Regulator_Linear", "Connector_USB", "Connector",
})


def _is_rejected(comp, rejected_ids):
    return (comp.get("id_str", "") in rejected_ids or
            comp.get("ref_des", "") in rejected_ids)


def _is_devkit_redundant(comp):
    """Return True if the component belongs to a library that devkits already
    provide on-board (regulators, USB bridges, USB connectors).  Replacing
    these is wrong — the devkit handles it."""
    id_str = (comp.get("id_str", "") or "").upper()
    lib = id_str.split(":")[0] if ":" in id_str else ""
    return lib in _DEVKIT_REDUNDANT_LIBS


def _filter_repair_candidates(subsystem: str, prompt: str, candidates: list[dict], rejected_ids: set[str], rejected_families: set[str], failing_component: dict | None = None, primary_mcu: str = "") -> list[dict]:
    filtered = []
    expected = _expected_buckets({"subsystem": subsystem, "function": subsystem, "example_components": []}, prompt=prompt)
    if not expected and failing_component:
        expected = _candidate_buckets(failing_component)
    
    is_mcu_sub = any(kw in subsystem.lower() for kw in ("mcu", "microcontroller", "processing")) or (failing_component and "mcu" in _candidate_buckets(failing_component))
    
    for candidate in candidates:
        if _is_rejected(candidate, rejected_ids):
            continue
        family = _normalize_part_family(candidate.get("id_str", ""))
        if family and (family in rejected_families or family.upper() in {f.upper() for f in rejected_families}):
            continue
        if expected and not (_candidate_buckets(candidate) & expected):
            continue
        # Restrict MCU replacements to the primary locked MCU family
        if is_mcu_sub and primary_mcu:
            cand_id = (candidate.get("id_str", "") or "").upper()
            if primary_mcu.upper() not in cand_id and not _normalize_part_family(cand_id).startswith("MCU:" + primary_mcu.upper()):
                continue
        filtered.append(candidate)
    return filtered


def validate_repair_node(state, config):
    comps = state.get("selected_components", [])
    rejected_ids = set(state.get("rejected_ids", []))
    rejected_families = set(state.get("rejected_families", []))
    research = state.get("research_results", [])
    retry_count = state.get("retry_count", 0)
    repair_failures = list(state.get("repair_failures", []))

    if not rejected_ids:
        return _stage_result(state, "validate_repair", {
            "selected_components": comps,
            "retry_count": retry_count + 1,
        })

    def _should_preserve(c: dict) -> bool:
        if c.get("is_user_locked") or c.get("user_locked") or c.get("functional_id"):
            return True
        cat = c.get("category", "")
        if cat in ("Connector", "Switch", "Device", "Power_Protection"):
            return True
        if _is_passive(c.get("id_str", ""), cat):
            return True
        return not _is_rejected(c, rejected_ids)

    failing = [c for c in comps if not _should_preserve(c)]
    passing = [c for c in comps if _should_preserve(c)]
    unrecognized = rejected_ids - {c.get("id_str", "") for c in comps} - {c.get("ref_des", "") for c in comps}

    if unrecognized:
        _emit(config, "agent:log", {
            "message": f"  Repair: {len(unrecognized)} rejected_id(s) don't match any component — likely structural errors: {sorted(unrecognized)}"
        })

    _emit(config, "agent:log", {
        "message": f"  Repair: preserving {len(passing)} component(s), replacing {len(failing)} failed one(s)"
    })

    if not failing:
        return _stage_result(state, "validate_repair", {
            "selected_components": comps,
            "retry_count": retry_count + 1,
        })

    emit_assistant_message(config, f"Replacing {len(failing)} failed component(s) from validation...")
    emit_tool_event(config, "Validation Repair", "running",
                    f"Replacing {len(failing)} failed component(s)")

    replacements = []
    subsystem_map = {}
    for sub in research:
        name = sub.get("subsystem", "")
        if name:
            subsystem_map[name] = sub.get("results", [])

    for c in failing:
        subsystem = c.get("subsystem", "")
        # Skip replacing devkit-redundant components (regulator, USB bridge,
        # USB connector) — the devkit already provides these on-board.
        # Replacing them with a different part just creates electrical errors.
        if _is_devkit_redundant(c):
            _emit(config, "agent:log", {
                "message": f"  Repair: skipping {c.get('id_str', '?')} [{c.get('ref_des', '?')}] — devkit-redundant, no replacement needed"
            })
            continue
        primary_mcu = state.get("primary_mcu", "")
        candidates = subsystem_map.get(subsystem, [])
        candidates = _filter_repair_candidates(
            subsystem,
            state.get("prompt", ""),
            candidates,
            rejected_ids,
            rejected_families,
            failing_component=c,
            primary_mcu=primary_mcu,
        )
        best = None
        if candidates:
            from agent.reranker import rank_candidates
            sub_data = {"subsystem": subsystem, "results": candidates}
            ranked = rank_candidates(
                sub_data, candidates,
                existing_components=passing + replacements,
                user_prompt=state.get("prompt", ""),
                config=config,
            )
            best = ranked[0] if ranked else None
        if not best and _is_passive(c.get("id_str", ""), c.get("category", "")):
            # Keep original standard passive rather than replacing with an invalid trimmer or random part
            best = c
        if not best:
            query = c.get("description", c.get("id_str", ""))
            try:
                results = search_components(query, k=3)
                results = _filter_repair_candidates(
                    subsystem,
                    state.get("prompt", ""),
                    results,
                    rejected_ids,
                    rejected_families,
                    failing_component=c,
                    primary_mcu=primary_mcu,
                )
                # Filter out trimmers or incompatible parts
                results = [r for r in results if "R_TRIM" not in r.get("id_str", "").upper()]
                best = results[0] if results else None
            except Exception:
                best = None
        if best:
            replacements.append({
                "id_str": best["id_str"],
                "ref_des": "",
                "category": best.get("category", best["id_str"].split(":")[0] if ":" in best["id_str"] else "General"),
                "description": best.get("text", best.get("description", c.get("description", ""))),
                "justification": c.get("justification", ""),
                "datasheet_text": "",
                "subsystem": subsystem,
            })
            _emit(config, "agent:log", {
                "message": f"  Repair: {c.get('id_str', '?')} [{c.get('ref_des', '?')}] → {best['id_str']} for '{subsystem}'"
            })
        else:
            repair_failures.append(f"{subsystem}:{c.get('id_str', '')}")
            _emit(config, "agent:log", {
                "message": f"  Repair: no replacement for {c.get('id_str', '?')} [{c.get('ref_des', '?')}] — removing"
            })

    new_comps = passing + replacements

    existing_refs = {comp["ref_des"] for comp in new_comps if comp.get("ref_des")}
    for comp in new_comps:
        if comp.get("ref_des"):
            continue
        prefix = _ref_prefix(comp.get("category", ""), comp.get("id_str", ""))
        num = 1
        while f"{prefix}{num}" in existing_refs:
            num += 1
        comp["ref_des"] = f"{prefix}{num}"
        existing_refs.add(comp["ref_des"])

    _emit(config, "agent:log", {
        "message": f"  Repair: {len(new_comps)} components after repair ({len(passing)} preserved, {len(replacements)} replaced)"
    })
    emit_tool_event(config, "Validation Repair", "completed",
                    f"{len(passing)} preserved, {len(replacements)} replaced")

    return _stage_result(state, "validate_repair", {
        "selected_components": new_comps,
        "retry_count": retry_count + 1,
        "repair_failures": repair_failures,
    })
