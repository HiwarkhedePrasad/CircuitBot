import re

from agent.datasheet import extract_critical_specs
from agent.reranker import rank_candidates
from agent.support_rules import get_supporting_components, resolve_fallback_symbol
from agent.tools import search_components, fetch_footprint
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result,
    _extract_part_numbers, _is_passive, _sanitize_data,
)

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
        if existing and re.fullmatch(r'[A-Z]+\d+', existing):
            m = re.match(r'([A-Z]+)(\d+)', existing)
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
        if existing and re.fullmatch(r'[A-Z]+\d+', existing):
            ref_counts[existing] = ref_counts.get(existing, 0) + 1

    # Pass 3: Pre-register all unique valid refs to seen_refs
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'[A-Z]+\d+', existing) and ref_counts.get(existing, 0) == 1:
            seen_refs.add(existing)

    # Pass 4: assign refs — keep unique valid refs, generate new ones for collisions, invalid, or empty refs
    for comp in components:
        existing = comp.get('ref_des', '')
        if existing and re.fullmatch(r'[A-Z]+\d+', existing) and ref_counts.get(existing, 0) == 1:
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


def select_node(state, config):
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})
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

    rejected_ids = set(state.get("rejected_ids", []))
    for sub in research:
        if rejected_ids:
            before = len(sub.get("results", []))
            sub["results"] = [r for r in sub["results"] if r["id_str"] not in rejected_ids]
            dropped = before - len(sub["results"])
            if dropped:
                _emit(config, "agent:log", {
                    "message": f"  Rejected {dropped} previously-failed component(s) for '{sub.get('subsystem', '')}'"
                })

    selected = []
    existing_ids = {c["id_str"] for c in state.get("selected_components", [])}
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
             and c.get("id_str") not in rejected_ids),
            None
        )
        if match:
            subs_to_skip.add(sub_name)
            selected.append(match)
            _emit(config, "agent:log", {
                "message": f"  Preserved {match['id_str']} for '{sub_name}' (validator already fixed this)"
            })

    research = [sub for sub in research if sub["subsystem"] not in subs_to_skip]

    _emit(config, "agent:thinking", {"message": f"Scoring candidates across {len(research)} subsystem(s)..."})
    for sub in research:
        candidates = sub.get("results", [])
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
        # Deterministic user-requested part override: if the user named a
        # specific part number (e.g. "DS18B20") and it exists somewhere in
        # the ranked list but wasn't picked, promote it to #1.  This ensures
        # user-requested parts are never ignored regardless of LLM behavior.
        user_parts = _extract_part_numbers(state.get("prompt", ""))
        if user_parts and ranked:
            up_upper = [p.strip().upper() for p in user_parts]
            for rank_idx, cand in enumerate(ranked):
                cid = cand.get("id_str", "").upper()
                if rank_idx > 0 and any(p in cid for p in up_upper):
                    _emit(config, "agent:log", {
                        "message": f"  User-requested part {cand['id_str']} at rank #{rank_idx+1} "
                                   f"— promoting to #1 (user named this part in prompt)"
                    })
                    ranked.insert(0, ranked.pop(rank_idx))
                    break
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
            selected.append({
                "id_str": best["id_str"],
                "ref_des": "",  # assigned in dedup step
                "category": best.get("category", best["id_str"].split(":")[0] if ":" in best["id_str"] else "General"),
                "description": best.get("text", best.get("description", "")),
                "justification": best.get("justification", ""),
                "datasheet_text": "",
                "subsystem": sub.get("subsystem", ""),
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
        if c.get("justification", "").startswith("Auto-added by validator"):
            selected.append(c)
            carried += 1
            _emit(config, "agent:log", {
                "message": f"  Preserved {c['id_str']} (validator-added, carried forward)"
            })
        elif c.get("subsystem", "") in research_names:
            selected.append(c)
            carried += 1
            _emit(config, "agent:log", {
                "message": f"  Preserved {c['id_str']} for '{c['subsystem']}' (reranker skipped, previous selection carried forward)"
            })
    if carried:
        selected = _assign_ref_des(selected, subsystem_sheet_map)
        _emit(config, "agent:log", {"message": f"  Carried forward {carried} component(s)"})

    # Dedup by subsystem label: if two non-passive components share the same
    # subsystem (e.g., bare IC + module for same role across retry cycles),
    # keep only the first one (which is the higher-ranked original pick).
    # IMPORTANT: skip validator-added components — they are supporting ICs
    # (USB-UART bridge, fuse, ESD diodes) that genuinely share the subsystem
    # label with the main IC they support. Deduping them would remove valid
    # connectivity (e.g., the USB bridge needed for ATmega USB communication).
    seen_subs: dict[str, str] = {}
    deduped_subs: list[dict] = []
    for c in selected:
        if c.get("justification", "").startswith("Auto-added by validator"):
            deduped_subs.append(c)
            continue
        sub = c.get("subsystem", "")
        if not sub:
            deduped_subs.append(c)
            continue
        if sub in seen_subs:
            _emit(config, "agent:log", {
                "message": f"  Dedup: dropped {c['ref_des']} ({c.get('id_str', '?')}) — "
                           f"subsystem '{sub}' already has {seen_subs[sub]}"
            })
            continue
        seen_subs[sub] = f"{c['ref_des']} ({c.get('id_str', '?')})"
        deduped_subs.append(c)
    if len(deduped_subs) < len(selected):
        selected = _assign_ref_des(deduped_subs, subsystem_sheet_map)
        _emit(config, "agent:log", {"message": f"  After dedup: {len(selected)} component(s)"})

    covered_prefixes_by_subsystem = {}
    for c in selected:
        if c.get("justification", "").startswith("Auto-added by validator") and c.get("subsystem"):
            prefix = ''.join(ch for ch in c.get("ref_des", "") if ch.isalpha())
            covered_prefixes_by_subsystem.setdefault(c["subsystem"], set()).add(prefix)

    _emit(config, "agent:thinking", {"message": "Fetching datasheets for selected components..."})
    for s in selected:
        id_str = s["id_str"]
        if s.get("datasheet_text"):
            continue
        url = ""
        for sub in research:
            for r in sub.get("results", []):
                if r["id_str"] == id_str:
                    url = r.get("datasheet", "")
                    break
            if url:
                break
        if url:
            snippet = _sanitize_data(
                extract_critical_specs(url),
                label=f"datasheet:{s['id_str']}"
            )
            if snippet:
                s["datasheet_text"] = snippet
                _emit(config, "agent:log", {
                    "message": f"  Fetched datasheet ({len(snippet)} chars) for {s['ref_des']} ({id_str})"
                })

    _emit(config, "agent:thinking", {"message": "Adding supporting components..."})
    support_parts = []
    for s in selected:
        parts = get_supporting_components(s)
        covered = covered_prefixes_by_subsystem.get(s.get("subsystem", ""), set())
        for p in parts:
            if p["ref_des_prefix"] in covered:
                _emit(config, "agent:log", {
                    "message": f"  Skipped {p['description']} for {s['ref_des']} — '{s.get('subsystem','')}' already has a validator-fixed {p['ref_des_prefix']}-part"
                })
                continue
            count = p.get("count", 1)
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
    if injected:
        selected.extend(injected)
        selected = _assign_ref_des(selected, subsystem_sheet_map)
        _emit(config, "agent:log", {
            "message": f"  Injected {len(injected)} supporting components"
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
            except Exception:
                pass

    _emit(config, "agent:log", {
        "message": f"Selected {len(selected)} components: " +
                   ", ".join(f'{s["ref_des"]}={s["id_str"].split(":")[-1][:20]}' for s in selected)
    })
    part_names = [f'{s["ref_des"]}={s["id_str"].split(":")[-1]}' for s in selected if s.get("id_str")]
    emit_tool_event(config, "Component Selection", "completed", f"Selected {len(selected)} components")
    emit_assistant_message(config, f"Selected {len(selected)} components: {', '.join(part_names)}.")
    return _stage_result(state, "select", {
        "selected_components": selected,
        "retry_count": retry_count + 1,
    })
