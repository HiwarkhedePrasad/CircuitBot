import re

from agent.reranker import rank_candidates
from agent.support_rules import get_supporting_components, resolve_fallback_symbol
from agent.tools import search_components, fetch_footprint
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result,
    _extract_part_numbers, _is_passive, emit_thought, emit_tool_call, emit_tool_end, emit_step,
)
from agent.knowledge.part_locker import extract_and_lock_user_components
from agent.synthesis.support_synthesizer import synthesize_support_components
from uuid import uuid4

_PREFIX_RULES: list[tuple[str, str]] = [
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
]

_TYPE_BUCKET_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("resistor", ("RESISTOR", "R_SMALL", ":R_", " OHM")),
    ("capacitor", ("CAPACITOR", "C_SMALL", ":C_", "UF", "NF", "PF")),
    ("inductor", ("INDUCTOR", "L_SMALL", ":L_")),
    ("diode", ("DIODE", ":D_", "SCHOTTKY", "ZENER")),
    ("led", ("DEVICE:LED", " LED ", "INDICATOR")),
    ("connector", ("CONNECTOR", "HEADER", "TERMINAL", "RECEPTACLE")),
    ("mcu", ("MCU_", "MICROCONTROLLER", "PROCESSOR", "ESP32", "STM32", "RP2040", "RP2350", "ATMEGA", "ATTINY")),
    ("sensor", ("SENSOR", "TMP", "BME", "DS18", "DHT")),
    ("regulator_switching", ("REGULATOR_SWITCHING", "BUCK", "BOOST", "STEP-DOWN", "STEP DOWN", "STEP-UP", "STEP UP", "SWITCHING REGULATOR")),
    ("regulator_linear", ("REGULATOR_LINEAR", "LDO", "LINEAR REGULATOR", "AMS1117")),
    ("usb_uart", ("CP210", "CH340", "FT232", "FT230", "USB-UART", "USB TO UART", "UART BRIDGE")),
    ("driver", ("DRIVER", "H-BRIDGE", "MOTOR DRIVER", "LED DRIVER")),
    ("crystal", ("CRYSTAL", "OSCILLATOR", "RESONATOR")),
]

_SUBSYSTEM_EXPECTATION_HINTS: list[tuple[str, set[str]]] = [
    ("power", {"regulator_switching", "regulator_linear"}),
    ("sensor", {"sensor"}),
    ("temperature", {"sensor"}),
    ("lm35", {"sensor"}),
    ("microcontroller", {"mcu"}),
    ("mcu", {"mcu"}),
    ("wireless", {"mcu", "driver"}),
    ("status indicator", {"led"}),
    ("passive", {"resistor", "capacitor", "inductor", "diode"}),
    ("connector", {"connector"}),
]


def _candidate_text(candidate: dict) -> str:
    return " ".join(str(candidate.get(key, "") or "") for key in ("id_str", "category", "text", "description")).upper()


def _normalize_part_family(id_str: str) -> str:
    text = (id_str or "").upper()
    if not text:
        return ""
    lib, _, part = text.partition(":")
    part = part or lib
    if lib.startswith("DEVICE"):
        if part.startswith("R_") or part == "R_SMALL":
            return "DEVICE:RESISTOR"
        if part.startswith("C_") or part == "C_SMALL":
            return "DEVICE:CAPACITOR"
        if part.startswith("L_") or part == "L_SMALL":
            return "DEVICE:INDUCTOR"
        if part.startswith("D_") or part == "D_SMALL":
            return "DEVICE:DIODE"
        if part == "LED":
            return "DEVICE:LED"
    if any(token in text for token in ("CP2102", "CP2104")):
        return "INTERFACE_USB:CP2102"
    if "CH340" in text:
        return "INTERFACE_USB:CH340"
    if "FT232" in text or "FT230" in text:
        return "INTERFACE_USB:FTDI_UART"
    if "STM32" in text:
        return "MCU:STM32"
    if "ESP32" in text or "ESP8266" in text:
        return "MCU:ESP32"
    if "RP2040" in text:
        return "MCU:RP2040"
    if "RP2350" in text:
        return "MCU:RP2350"
    if "DS18B20" in text:
        return "SENSOR:DS18B20"
    if "TMP117" in text:
        return "SENSOR:TMP117"
    if any(token in text for token in ("BUCK", "STEP-DOWN", "STEP DOWN", "SWITCHING")) or lib == "REGULATOR_SWITCHING":
        return f"{lib or 'REGULATOR'}:SWITCHING"
    if "LDO" in text or "AMS1117" in text or lib == "REGULATOR_LINEAR":
        return f"{lib or 'REGULATOR'}:LINEAR"
    base = re.split(r"[-_/]", part)[0]
    return f"{lib}:{base}" if lib else base


def _candidate_buckets(candidate: dict) -> set[str]:
    text = _candidate_text(candidate)
    buckets: set[str] = set()
    for bucket, patterns in [
        ("resistor", ("RESISTOR", "R_SMALL", ":R_", " OHM")),
        ("capacitor", ("CAPACITOR", "C_SMALL", ":C_", "UF", "NF", "PF")),
        ("inductor", ("INDUCTOR", "L_SMALL", ":L_")),
        ("diode", ("DIODE", ":D_", "SCHOTTKY", "ZENER")),
        ("led", ("DEVICE:LED", " LED ", "INDICATOR")),
        ("display", ("DISPLAY", "OLED", "SSD1306", "SSH1106", "LCD", "SEGMENT")),
        ("connector", ("CONNECTOR", "HEADER", "TERMINAL", "RECEPTACLE")),
        ("mcu", ("MCU_", "MICROCONTROLLER", "PROCESSOR", "ESP32", "STM32", "RP2040", "RP2350", "ATMEGA", "ATTINY")),
        ("sensor", ("SENSOR", "TMP", "BME", "DS18", "DHT", "LM35", "AHT", "SHT", "MPU")),
        ("regulator_switching", ("REGULATOR_SWITCHING", "BUCK", "BOOST", "STEP-DOWN", "STEP DOWN", "STEP-UP", "STEP UP", "SWITCHING REGULATOR", "TPS")),
        ("regulator_linear", ("REGULATOR_LINEAR", "LDO", "LINEAR REGULATOR", "AMS1117", "AP2112", "LM1117", "MCP1700")),
        ("usb_uart", ("CP210", "CH340", "FT232", "FT230", "USB-UART", "USB TO UART", "UART BRIDGE", "USB INTERFACE", "USB SERIAL")),
    ]:
        if any(pattern in text for pattern in patterns):
            buckets.add(bucket)
    return buckets


def _expected_buckets(sub: dict, prompt: str = "") -> set[str]:
    subsystem = (sub.get("subsystem", "") or "").lower()
    import re
    if "user-specified" in subsystem:
        match = re.search(r'user-specified\s*\(([^)]+)\)', subsystem, re.IGNORECASE)
        if match:
            part_name = match.group(1).strip().upper()
            if "LM35" in part_name:
                return {"sensor"}
            elif "AMS1117" in part_name or "AP2112" in part_name:
                return {"regulator_linear"}
            elif "ESP32" in part_name or "STM32" in part_name:
                return {"mcu"}

    function = (sub.get("function", "") or "").lower()
    examples = " ".join(str(x) for x in (sub.get("example_components", []) or []))
    text = f"{subsystem} {function} {examples} {prompt}".upper()
    expected: set[str] = set()
    for keyword, buckets in _SUBSYSTEM_EXPECTATION_HINTS:
        if keyword.upper() in text:
            expected.update(buckets)
    if "LM35" in text:
        expected.add("sensor")
    if "BUCK" in text or "STEP-DOWN" in text or "STEP DOWN" in text:
        expected.discard("regulator_linear")
        expected.add("regulator_switching")
    if "LDO" in text or "LINEAR REGULATOR" in text:
        expected.discard("regulator_switching")
        expected.add("regulator_linear")
    if "RESISTOR" in text or "OHM" in text:
        return {"resistor"}
    if "CAPACITOR" in text or "UF" in text or "NF" in text or "PF" in text:
        return {"capacitor"}
    if "INDUCTOR" in text:
        return {"inductor"}
    if "LED" in text and "DRIVER" not in text:
        return {"led"}
    return expected


def _filter_candidates_by_expected_type(sub: dict, candidates: list[dict], prompt: str = "") -> list[dict]:
    expected = _expected_buckets(sub, prompt)
    if not expected:
        return candidates
    filtered = [candidate for candidate in candidates if _candidate_buckets(candidate) & expected]
    return filtered or candidates


def _ref_prefix(category: str, id_str: str = '') -> str:
    text = f"{category} {id_str}".upper()
    for keyword, prefix in _PREFIX_RULES:
        if keyword in text:
            return prefix
    return 'U'


def _assign_ref_des(components: list[dict], sheet_map: dict[str, int] | None = None) -> list[dict]:
    """Assign reference designators using sheet×100 numbering when a sheet_map
    is provided.  Components without a subsystem entry get legacy sequential
    numbering (no sheet base).

    sheet_map: {subsystem_name: sheet_number} — e.g. {"Power Input": 1, "MCU": 2}
    """
    counters: dict[tuple[str, int], int] = {}  # (letter, sheet) -> counter
    seen_refs: set[str] = set()

    def _sheet_of(comp: dict) -> int:
        if not sheet_map:
            return 0
        sub = comp.get("subsystem", "") or ""
        return sheet_map.get(sub, 0)

    def _base(sheet: int) -> int:
        return sheet * 100 if sheet > 0 else 0

    # Pass 1: register max counter from existing valid refs
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'#?[A-Z]+\d+', existing):
            m = re.match(r'#?([A-Z]+)(\d+)', existing)
            if m:
                letter = m.group(1)
                num = int(m.group(2))
                sheet = _sheet_of(comp)
                base = _base(sheet)
                # Only track counters that belong to this sheet's range
                if base == 0 or (base < num < base + 100):
                    key = (letter, sheet)
                    counters[key] = max(counters.get(key, 0), num - base)

    # Pass 2: count how many components share each ref
    ref_counts: dict[str, int] = {}
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'#?[A-Z]+\d+', existing):
            ref_counts[existing] = ref_counts.get(existing, 0) + 1

    # Pass 3: Pre-register all unique valid refs to seen_refs
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'#?[A-Z]+\d+', existing) and ref_counts.get(existing, 0) == 1:
            seen_refs.add(existing)

    # Pass 4: assign refs — keep unique valid refs, generate new ones for collisions, invalid, or empty refs
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'#?[A-Z]+\d+', existing) and ref_counts.get(existing, 0) == 1:
            continue
        cat = comp.get('category', '')
        id_str = comp.get('id_str', '')
        letter = _ref_prefix(cat, id_str)
        sheet = _sheet_of(comp)
        base = _base(sheet)
        key = (letter, sheet)
        counters[key] = counters.get(key, 0) + 1
        new_ref = f"{letter}{base + counters[key]}"
        while new_ref in seen_refs:
            counters[key] += 1
            new_ref = f"{letter}{base + counters[key]}"
        comp['ref_des'] = new_ref
        seen_refs.add(new_ref)
    return components


def _dedupe_selected_components(selected: list[dict], subsystem_sheet_map: dict[str, int], config) -> list[dict]:
    # Dedup by functional_id / unique description key first
    seen_keys: dict[str, str] = {}
    deduped_ids: list[dict] = []
    for c in selected:
        func_id = c.get("functional_id", "")
        id_str = c.get("id_str", "")
        desc = c.get("description", "")
        key = func_id if func_id else f"{id_str}::{desc}"
        
        # If no key available, keep it
        if not key or key == "::":
            deduped_ids.append(c)
            continue
            
        # Passive devices with unique descriptions are always preserved
        if _is_passive(id_str, c.get("category", "")) and desc:
            deduped_ids.append(c)
            continue

        if key in seen_keys:
            _emit(config, "agent:log", {
                "message": f"  Dedup: dropped {c.get('ref_des', '?')} ({id_str}) -- duplicate of {seen_keys[key]}"
            })
            continue
        seen_keys[key] = f"{c.get('ref_des', '?')} (subsystem: {c.get('subsystem', '?')})"
        deduped_ids.append(c)
    if len(deduped_ids) < len(selected):
        selected = _assign_ref_des(deduped_ids, subsystem_sheet_map)
        _emit(config, "agent:log", {"message": f"  After ID dedup: {len(selected)} component(s)"})

    seen_subs: dict[str, str] = {}
    deduped_subs: list[dict] = []
    for c in selected:
        id_str = c.get("id_str", "")
        category = c.get("category", "")
        justification = c.get("justification", "")
        func_id = c.get("functional_id", "")
        
        # Always keep passives, switches, connectors, and support components
        if (
            _is_passive(id_str, category)
            or category in ("Switch", "Connector", "Device", "Power_Protection")
            or func_id
            or justification.startswith("Auto-added by validator")
            or justification.startswith("Deterministically synthesized")
            or justification.startswith("Supporting part")
            or c.get("for_component")
        ):
            deduped_subs.append(c)
            continue
        sub = c.get("subsystem", "")
        if not sub:
            deduped_subs.append(c)
            continue
        if sub in seen_subs:
            _emit(config, "agent:log", {
                "message": f"  Dedup: dropped {c.get('ref_des', '?')} ({c.get('id_str', '?')}) -- "
                           f"subsystem '{sub}' already has {seen_subs[sub]}"
            })
            continue
        seen_subs[sub] = f"{c.get('ref_des', '?')} ({c.get('id_str', '?')})"
        deduped_subs.append(c)
    if len(deduped_subs) < len(selected):
        selected = _assign_ref_des(deduped_subs, subsystem_sheet_map)
        _emit(config, "agent:log", {"message": f"  After dedup: {len(selected)} component(s)"})
    return selected


def select_node(state, config):
    sel_id = uuid4().hex[:8]
    emit_tool_call(config, sel_id, "Component Selection", "running")
    emit_thought(config, "Selecting best components...")
    emit_assistant_message(config, "Scoring and ranking candidates to select the best components for each subsystem...")
    emit_tool_event(config, "Component Selection", "running", "Scoring and ranking candidates...")
    contract = _check_stage_contract("select", state, ["research_results", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "select", {"selected_components": []})
    retry_count = state.get("retry_count", 0)
    research = state.get("research_results", [])
    if not research:
        _emit(config, "agent:log", {"message": "No research results to select from."})
        return _stage_result(state, "select", {"selected_components": []})

    prompt = state.get("prompt", "")
    locked_user_parts = extract_and_lock_user_components(prompt)
    if locked_user_parts:
        _emit(config, "agent:log", {
            "message": f"  Part Locker: Hard-locked {len(locked_user_parts)} user-specified component(s) into state"
        })

    rejected_ids = set(state.get("rejected_ids", []))
    rejected_families = set(state.get("rejected_families", []))
    existing_ids = {c.get("id_str", "") for c in state.get("selected_components", [])}
    for sub in research:
        if rejected_ids or rejected_families:
            before = len(sub.get("results", []))
            sub["results"] = [
                r for r in sub["results"]
                if r["id_str"] not in rejected_ids
                and _normalize_part_family(r.get("id_str", "")) not in rejected_families
            ]
            dropped = before - len(sub["results"])
            if dropped:
                _emit(config, "agent:log", {
                    "message": f"  Rejected {dropped} previously-failed component(s) for '{sub.get('subsystem', '')}'"
                })

    selected = []
    research.sort(key=lambda s: 0 if any(k in s.get('subsystem', '').lower() for k in ['mcu', 'processing', 'microcontroller', 'core']) else 1)

    _GENERIC_PASSIVES = frozenset([
        "Device:R_Small", "Device:C_Small", "Device:LED",
        "Device:L_Small", "Device:D_Small",
    ])
    subs_to_skip = set()
    for sub in research:
        sub_name = sub.get("subsystem", "")
        match = next(
            (c for c in state.get("selected_components", [])
             if c.get("subsystem") == sub_name
             and c.get("id_str") not in _GENERIC_PASSIVES
             and c.get("id_str") not in rejected_ids
             and _normalize_part_family(c.get("id_str", "")) not in rejected_families
             and c.get("justification", "").startswith("Auto-added by validator")),
            None
        )
        if match:
            selected.append(match)
            _emit(config, "agent:log", {
                "message": f"  Preserved {match['id_str']} for '{sub_name}' (validator-added support part)"
            })


    emit_thought(config, f"Scoring candidates across {len(research)} subsystem(s)...")
    for sub in research:
        sub_name = sub.get("subsystem", "")
        if sub_name.lower() in ("passive components", "connectors") and not sub.get("example_components"):
            _emit(config, "agent:log", {
                "message": f"  Skipping IC candidate selection for generic support subsystem '{sub_name}' (handled by support rules)"
            })
            continue
        emit_step(config, sel_id, f"Scoring {sub_name}...", "running")
        candidates = _filter_candidates_by_expected_type(sub, sub.get("results", []), state.get("prompt", ""))

        # Filter out components matching the template avoid list
        avoid_list = sub.get("_avoid", [])
        if avoid_list:
            avoid_lower = [a.lower() for a in avoid_list]
            original_count = len(candidates)
            candidates = [
                c for c in candidates
                if not any(
                    avoid_term in (c.get("id_str", "") + " " + c.get("text", "")).lower()
                    for avoid_term in avoid_lower
                )
            ]
            if len(candidates) < original_count:
                _emit(config, "agent:log", {
                    "message": f"  Filtered {original_count - len(candidates)} avoid-listed component(s)"
                })
        if not candidates:
            rj = state.get("rejected_ids", [])
            reason = "all candidates rejected by validator — no substitute available" if rj else "no candidates found"
            _emit(config, "agent:log", {
                "message": f"  No candidates for '{sub.get('subsystem', '')}' — {reason}"
            })
            continue
        ranked = rank_candidates(
            sub, candidates,
            existing_components=selected,
            user_prompt=state.get("prompt", ""),
            config=config,
        )
        # Dev-board preference for prototyping prompts: when the user says
        # "ESP32 with button" (simple MCU + peripherals), prefer dev boards
        # over bare modules since they include USB, regulator, etc.
        prompt_lower = state.get("prompt", "").lower()
        _SIMPLE_MCU_KW = {"esp32", "arduino", "rp2040", "stm32", "nrf52"}
        _PROTO_KW = {"with", "and", "button", "led", "sensor", "simple", "basic"}
        is_prototyping = (
            any(mcu in prompt_lower for mcu in _SIMPLE_MCU_KW) and
            any(kw in prompt_lower for kw in _PROTO_KW) and
            len(research) <= 6
        )
        if is_prototyping and ranked:
            for cand in ranked:
                cid = cand.get("id_str", "").upper()
                if any(kw in cid for kw in ("DEVKIT", "DEV_KIT", "NODEMCU")):
                    cand["score"] = cand.get("score", 0) + 2
            # Re-sort by score descending
            ranked.sort(key=lambda c: c.get("score", 0), reverse=True)

        # Deterministic user-requested part override: if the user named a
        # specific part number (e.g. "DS18B20") and it exists somewhere in
        # the ranked list but wasn't picked, promote it to #1.  This ensures
        # user-requested parts are never ignored regardless of LLM behavior.
        user_parts = _extract_part_numbers(state.get("prompt", ""))
        if user_parts and ranked:
            up_upper = [p.strip().upper() for p in user_parts]
            # H-02: exact match first (full id_str), then exact match on the
            # part after the library prefix, then substring fallback.
            found_idx = None
            for rank_idx, cand in enumerate(ranked):
                cid = cand.get("id_str", "").upper()
                if rank_idx == 0:
                    continue
                for p in up_upper:
                    if cid == p:
                        found_idx = rank_idx
                        break
                if found_idx is not None:
                    break
            # Second pass: exact match against the part AFTER the library prefix
            if found_idx is None:
                for rank_idx, cand in enumerate(ranked):
                    cid = cand.get("id_str", "").upper()
                    if rank_idx == 0:
                        continue
                    cid_part = cid.split(":", 1)[-1] if ":" in cid else cid
                    for p in up_upper:
                        if cid_part == p:
                            found_idx = rank_idx
                            break
                    if found_idx is not None:
                        break
            # Third pass: substring fallback (least preferred)
            if found_idx is None:
                for rank_idx, cand in enumerate(ranked):
                    cid = cand.get("id_str", "").upper()
                    if rank_idx == 0:
                        continue
                    for p in up_upper:
                        if p in cid:
                            found_idx = rank_idx
                            break
                    if found_idx is not None:
                        break
            if found_idx is not None:
                # Skip promotion if part type doesn't match subsystem
                # (e.g., don't promote ESP32 MCU for "Power Input" subsystem)
                _sub_lower = sub_name.lower()
                _part_lib = ranked[found_idx].get("id_str", "").split(":")[0].upper()
                _skip_promotion = False
                if _sub_lower in ("power input", "power regulation", "power supply"):
                    if _part_lib in ("MCU_", "RF_MODULE", "RF_", "MCU_ESPRESSIF"):
                        _skip_promotion = True
                if not _skip_promotion:
                    _emit(config, "agent:log", {
                        "message": f"  User-requested part {ranked[found_idx]['id_str']} at rank #{found_idx+1} "
                                   f"— promoting to #1 (user named this part in prompt)"
                    })
                    ranked.insert(0, ranked.pop(found_idx))
        best = ranked[0] if ranked else None
        if not best:
            if existing_ids:
                _emit(config, "agent:log", {
                    "message": f"  '{sub.get('subsystem', '')}' has no candidates — keeping existing components if any"
                })
            continue
        best_score = best.get("score", 0)
        best_just = (best.get("justification") or "").upper()
        if best_score >= 4 and "SKIPPED" not in best_just:
            # ── Functional-duplicate guard ──────────────────────────────
            # Prevent selecting a second IC from the same library family
            # (e.g. MCU_Espressif:ESP32-C3 when MCU_Espressif:ESP32-S3 is
            # already selected for a different subsystem).
            _best_id = best["id_str"]
            _bi = _best_id.rfind(":")
            _best_lib = _best_id[:_bi] if _bi >= 1 else ""
            _FUNCTIONAL_DEDUP_LIBS = frozenset([
                "MCU_Espressif", "MCU_ST", "MCU_Microchip", "MCU_Nordic",
                "MCU_NXP", "MCU_Texas", "MCU_Infineon",
                "RF_Module", "RF",
                "Interface_USB", "Interface_UART",
                "Regulator_Linear", "Regulator_Switching",
            ])
            if _best_lib in _FUNCTIONAL_DEDUP_LIBS:
                existing_match = next(
                    (s for s in selected
                     if s.get("id_str", "").startswith(f"{_best_lib}:")
                     and not _is_passive(s.get("id_str", ""), s.get("category", ""))),
                    None
                )
                if existing_match:
                    _emit(config, "agent:log", {
                        "message": f"  Skipped {_best_id} for '{sub.get('subsystem', '')}' "
                                   f"— {_best_lib} already selected: {existing_match['id_str']}"
                    })
                    continue
            is_user_part = bool(user_parts) and any(
                p.upper() in best["id_str"].upper() for p in user_parts
            )
            selected.append({
                "id_str": best["id_str"],
                "ref_des": "",  # assigned in dedup step
                "category": best.get("category", best["id_str"].split(":")[0] if ":" in best["id_str"] else "General"),
                "description": best.get("text", best.get("description", "")),
                "justification": best.get("justification", ""),
                "datasheet_text": best.get("datasheet_snippet", ""),
                "subsystem": sub.get("subsystem", ""),
                "user_locked": is_user_part,
                "footprint": best.get("footprint", ""),
                "pads": best.get("pads", []),
            })
            _emit(config, "agent:log", {
                "message": f"  Selected {best['id_str']} (score={best_score}) for '{sub.get('subsystem', '')}'"
            })
        else:
            reason = "SKIPPED (module override)" if "SKIPPED" in best_just else f"low score ({best_score})"
            _emit(config, "agent:log", {
                "message": f"  Skipped '{sub.get('subsystem', '')}' — {reason}"
            })

    # Build subsystem→sheet map for sheet×100 annotation (KiCad convention).
    # Each unique subsystem gets a sequential sheet number starting at 1.
    subsystem_sheet_map: dict[str, int] = {
        sub: i + 1 for i, sub in enumerate(
            dict.fromkeys(c.get("subsystem", "") for c in selected if c.get("subsystem"))
        )
    }

    seen_ids = set()
    deduped = []
    for s in selected:
        id_str = s["id_str"]
        category = s.get("category", "")
        passive = _is_passive(id_str, category)
        if not passive and id_str in seen_ids:
            _emit(config, "agent:log", {"message": f"  Skipped duplicate IC: {id_str}"})
            continue
        s.setdefault("justification", "")
        s.setdefault("datasheet_text", "")
        seen_ids.add(id_str)
        deduped.append(s)
    selected = _assign_ref_des(deduped, subsystem_sheet_map)

    prev_selected = state.get("selected_components", [])
    current_ids = {c["id_str"] for c in selected}
    research_names = {s["subsystem"] for s in research}
    carried = 0
    for c in prev_selected:
        if c["id_str"] in current_ids:
            continue
        if c.get("id_str", "") in rejected_ids:
            continue
        if _normalize_part_family(c.get("id_str", "")) in rejected_families:
            continue
        if c.get("justification", "").startswith("Auto-added by validator"):
            selected.append(c)
            carried += 1
            _emit(config, "agent:log", {
                "message": f"  Preserved {c['id_str']} (validator-added, carried forward)"
            })
    if carried:
        selected = _assign_ref_des(selected, subsystem_sheet_map)
        _emit(config, "agent:log", {"message": f"  Carried forward {carried} component(s)"})

    selected = _dedupe_selected_components(selected, subsystem_sheet_map, config)

    covered_prefixes_by_subsystem = {}
    for c in selected:
        if c.get("justification", "").startswith("Auto-added by validator") and c.get("subsystem"):
            prefix = ''.join(ch for ch in c.get("ref_des", "") if ch.isalpha())
            covered_prefixes_by_subsystem.setdefault(c["subsystem"], set()).add(prefix)

    for s in selected:
        if s.get("datasheet_text"):
            _emit(config, "agent:log", {
                "message": f"  Datasheet info available for {s['ref_des']} ({s['id_str']})"
            })

    emit_thought(config, "Adding supporting components...")
    support_parts = []
    for s in selected:
        if s.get("user_locked"):
            continue
        parts = get_supporting_components(s)
        covered = covered_prefixes_by_subsystem.get(s.get("subsystem", ""), set())
        for p in parts:
            p_desc = p.get("description", "").upper()
            if "CC1" in p_desc or "CC2" in p_desc or "SENSOR" in p_desc or "LM35" in p_desc:
                # Handled deterministically with unique functional IDs by SupportSynthesizer
                continue
            if p["ref_des_prefix"] in covered:
                _emit(config, "agent:log", {
                    "message": f"  Skipped {p['description']} for {s['ref_des']} — '{s.get('subsystem','')}' already has a validator-fixed {p['ref_des_prefix']}-part"
                })
                continue
            count = p.get("count", 1)
            sp_key = (p.get("preferred_id_str", ""), p["description"], s["ref_des"])
            if any((item.get("preferred_id_str", ""), item.get("description", ""), item.get("for_component", "")) == sp_key for item in support_parts):
                continue
            for _ in range(count):
                support_parts.append({
                    "search_query": p["search_query"],
                    "preferred_id_str": p.get("preferred_id_str", ""),
                    "library_filter": p.get("library_filter", ""),
                    "ref_des_prefix": p["ref_des_prefix"],
                    "description": p["description"],
                    "for_component": s["ref_des"],
                })
    injected = []
    if support_parts:
        _emit(config, "agent:log", {
            "message": f"  Need {len(support_parts)} supporting part(s)"
        })
        for sp in support_parts:
            try:
                lib_filter = sp.get("library_filter") or None
                candidates = search_components(sp["search_query"], k=8, library_filter=lib_filter)
                chosen = None
                if sp["preferred_id_str"]:
                    for c in candidates:
                        if c["id_str"] == sp["preferred_id_str"]:
                            chosen = c
                            break
                if not chosen:
                    pid = sp.get("preferred_id_str", "")
                    if pid:
                        mapped = resolve_fallback_symbol(pid)
                        final_id = mapped if mapped else pid
                        chosen = {
                            "id_str": final_id,
                            "category": final_id.split(":")[0] if ":" in final_id else "Device",
                            "text": sp.get("description", ""),
                            "footprint": "",
                            "pads": [],
                        }
                        _emit(config, "agent:log", {
                            "message": f"  Used fallback symbol {final_id} for {sp['description']} (not in RAG results)"
                        })
                if not chosen and candidates:
                    for c in candidates:
                        if c.get("footprint"):
                            if not lib_filter or c["id_str"].startswith(lib_filter + ":"):
                                chosen = c
                                break
                    if not chosen:
                        for c in candidates:
                            if not lib_filter or c["id_str"].startswith(lib_filter + ":"):
                                chosen = c
                                break
                if chosen:
                    # Dedup guard: skip if a non-passive IC with same library +
                    # base part number already exists in selected (e.g.,
                    # Interface_USB:CP2102N injected when CP2102N-Axx-xQFN20
                    # was already selected by the reranker).
                    _chosen_id = chosen["id_str"]
                    _ci = _chosen_id.rfind(":")
                    _chosen_lib = _chosen_id[:_ci] if _ci >= 1 else ""
                    _chosen_base = _chosen_id[_ci+1:].split("-")[0] if _ci >= 1 else _chosen_id

                    # Same-library-is-duplicate: if injecting into a library
                    # where ANY component of that library already exists in
                    # selected, treat it as a duplicate.  This catches
                    # different-variant USB-UART bridges (CP2102N vs CP2102C),
                    # regulators, and other interface ICs from the same
                    # functional library.
                    _DUPE_LIBS = frozenset(["Interface_USB", "Interface_UART",
                        "Regulator_Linear", "Regulator_Switching"])
                    if _chosen_lib in _DUPE_LIBS:
                        if any(
                            s.get("id_str", "").startswith(f"{_chosen_lib}:")
                            for s in selected
                        ):
                            _emit(config, "agent:log", {
                                "message": f"  Skipped {_chosen_id} — {_chosen_lib} already selected"
                            })
                            continue

                    # Exact base-part match guard (existing logic):
                    if _chosen_lib and _chosen_lib != "Device" and _chosen_lib not in _DUPE_LIBS:
                        if any(
                            s.get("id_str", "").startswith(f"{_chosen_lib}:") and
                            s["id_str"].split(":")[-1].split("-")[0] == _chosen_base
                            for s in selected
                        ):
                            _emit(config, "agent:log", {
                                "message": f"  Skipped {_chosen_id} — {_chosen_lib} {_chosen_base} already selected"
                            })
                            continue
                    _infer_category = {
                        "Device:C_Small": "CAPACITOR", "Device:R_Small": "RESISTOR",
                        "Device:Polyfuse": "POLYFUSE", "Device:L_Small": "INDUCTOR",
                        "Device:LED": "DIODE",
                    }
                    ref_prefix = sp["ref_des_prefix"]
                    ref = f"{ref_prefix}{len(injected) + 1}"
                    injected.append({
                        "id_str": chosen["id_str"],
                        "ref_des": ref,
                        "category": _infer_category.get(chosen["id_str"],
                            chosen["id_str"].split(":")[0] if ":" in chosen["id_str"] else "Device"),
                        "description": sp["description"],
                        "footprint": chosen.get("footprint", ""),
                        "pads": chosen.get("pads", []),
                        "justification": f"Supporting part for {sp['for_component']}: {sp['description']}",
                        "datasheet_text": "",
                        "for_component": sp["for_component"],
                    })
                    _emit(config, "agent:log", {
                        "message": f"  Added {ref} ({chosen['id_str']}) as {sp['description']}"
                    })
                else:
                    _emit(config, "agent:log", {
                        "message": f"  WARNING: no suitable component found for {sp['description']} (query='{sp['search_query']}', filter={lib_filter})"
                    })
            except Exception as e:
                _emit(config, "agent:log", {"message": f"Support component search failed (skipped): {e}"})

    if locked_user_parts:
        for lp in locked_user_parts:
            if not any(s.get("id_str") == lp["id_str"] for s in selected):
                selected.append(lp)
                _emit(config, "agent:log", {
                    "message": f"  Hard Lock: Added user-requested part {lp['id_str']} ({lp['subsystem']})"
                })

    synths = synthesize_support_components(selected, prompt)
    if synths:
        for sc in synths:
            if not any(s.get("functional_id") == sc.get("functional_id") for s in selected):
                selected.append(sc)
        _emit(config, "agent:log", {
            "message": f"  Synthesizer: Deterministically added {len(synths)} discrete support passives/switches"
        })

    if injected:
        selected.extend(injected)

    selected = _assign_ref_des(selected, subsystem_sheet_map)
    selected = _dedupe_selected_components(selected, subsystem_sheet_map, config)
    _emit(config, "agent:log", {
        "message": f"  Final selected components: {len(selected)} total"
    })

    for s in selected:
        if s.get("justification"):
            _emit(config, "agent:log", {
                "message": f"  {s['ref_des']} ({s['id_str']}): {s['justification']}"
            })

    fp_lookup = {}
    for sub in research:
        for r in sub.get("results", []):
            fp_lookup[r["id_str"]] = {
                "footprint": r.get("footprint") or "",
                "pads": r.get("pads") or [],
            }
    import logging
    logger = logging.getLogger(__name__)

    for s in selected:
        entry = fp_lookup.get(s["id_str"], {})
        if not s.get("footprint"):
            s["footprint"] = entry.get("footprint", "")
        if not s.get("pads"):
            s["pads"] = entry.get("pads", [])
        if not s["footprint"]:
            try:
                info = fetch_footprint(s["id_str"])
                if info:
                    s["footprint"] = info.get("footprint", "")
                    s["pads"] = info.get("pads", [])
            except Exception as e:
                logger.warning(f"Failed to fetch footprint for {s['id_str']}: {e}")
        if not s.get("footprint"):
            try:
                from kicad_rag.store import resolve_footprint_from_filters
                resolved = resolve_footprint_from_filters(s["id_str"])
                if resolved:
                    s["footprint"] = resolved
            except Exception as e:
                logger.warning(f"Failed to resolve footprint from filters for {s['id_str']}: {e}")
        if not s.get("footprint"):
            _cat, _, _name = s.get("id_str", "").partition(":")
            if _cat == "Device":
                if _name == "R":
                    s["footprint"] = "Resistor_SMD:R_0805_2012Metric"
                elif _name == "C":
                    s["footprint"] = "Capacitor_SMD:C_0805_2012Metric"
                elif _name == "C_Polarized":
                    s["footprint"] = "Capacitor_SMD:CP_Elec_4x5.3"
                elif _name == "L":
                    s["footprint"] = "Inductor_SMD:L_0805_2012Metric"
                elif _name.startswith("D_") or _name == "D":
                    s["footprint"] = "Diode_SMD:D_SOD-123"
                elif _name == "LED":
                    s["footprint"] = "LED_SMD:LED_0805_2012Metric"
            elif _cat == "Switch" and "SW_Push" in _name:
                s["footprint"] = "Button_Switch_SMD:SW_SPST_B3U-1000P"
            elif _cat in ("Transistor_BJT", "Transistor_FET"):
                s["footprint"] = "Package_TO_SOT_SMD:SOT-23"
        if not s.get("footprint"):
            logger.warning(
                f"Could not resolve footprint for {s['id_str']}. "
                f"Component will have empty footprint in PCB layout."
            )

    _emit(config, "agent:log", {
        "message": f"Selected {len(selected)} components: " +
                   ", ".join(f'{s["ref_des"]}={s["id_str"].split(":")[-1][:20]}' for s in selected)
    })
    part_names = [f'{s["ref_des"]}={s["id_str"].split(":")[-1]}' for s in selected if s.get("id_str")]
    emit_tool_event(config, "Component Selection", "completed", f"Selected {len(selected)} components")
    emit_tool_end(config, sel_id, f"Selected {len(selected)} components across {len(research)} subsystem(s)",
                   details=f"Components: {', '.join(part_names)}")
    emit_assistant_message(config, f"Selected {len(selected)} components: {', '.join(part_names)}.")
    return _stage_result(state, "select", {
        "selected_components": selected,
        "retry_count": retry_count + 1,
    })
