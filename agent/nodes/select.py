from agent.datasheet import fetch_datasheet_text
from agent.reranker import rank_candidates
from agent.support_rules import get_supporting_components
from agent.tools import search_components, fetch_footprint
from agent.utils import (
    _emit, _emit_activity, _check_stage_contract, _stage_result,
    _is_passive, _ref_prefix_for,
)


def select_node(state, config):
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})
    _emit_activity(config, "select", "Component Selection", "start")
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
             and c.get("id_str") not in _GENERIC_PASSIVES),
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
            _emit(config, "agent:log", {
                "message": f"  No candidates for '{sub.get('subsystem', '')}' — skipping"
            })
            continue
        ranked = rank_candidates(sub, candidates, existing_components=selected, config=config)
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

    seen_ids = set()
    seen_refs = set()
    deduped = []
    ref_counter = {}

    def _next_ref(prefix):
        n = ref_counter.get(prefix, 0) + 1
        while f"{prefix}{n}" in seen_refs:
            n += 1
        ref_counter[prefix] = n
        return f"{prefix}{n}"

    for s in selected:
        id_str = s["id_str"]
        category = s.get("category", "")
        passive = _is_passive(id_str, category)
        if not passive and id_str in seen_ids:
            _emit(config, "agent:log", {"message": f"  Skipped duplicate IC: {id_str}"})
            continue
        correct_prefix = _ref_prefix_for(id_str, category)
        current_prefix = ''.join(c for c in s.get("ref_des", "") if c.isalpha()) or 'U'
        if current_prefix != correct_prefix or s.get("ref_des", "") in seen_refs:
            old_ref = s.get("ref_des", "?")
            s["ref_des"] = _next_ref(correct_prefix)
            if old_ref != s["ref_des"]:
                _emit(config, "agent:log", {"message": f"  Renamed {old_ref} -> {s['ref_des']}"})
        else:
            num_part = ''.join(c for c in s["ref_des"] if c.isdigit())
            if num_part:
                ref_counter[correct_prefix] = max(ref_counter.get(correct_prefix, 0), int(num_part))
        s.setdefault("justification", "")
        s.setdefault("datasheet_text", "")
        seen_ids.add(id_str)
        seen_refs.add(s["ref_des"])
        deduped.append(s)
    selected = deduped

    prev_selected = state.get("selected_components", [])
    current_ids = {c["id_str"] for c in selected}
    research_names = {s["subsystem"] for s in research}
    carried = 0

    def _claim_carried_ref(c):
        ref = c.get("ref_des", "")
        id_str = c["id_str"]
        category = c.get("category", "")
        prefix = ''.join(ch for ch in ref if ch.isalpha()) or _ref_prefix_for(id_str, category)
        if not ref or ref in seen_refs:
            old_ref = ref or "?"
            c["ref_des"] = _next_ref(prefix)
            if old_ref != c["ref_des"]:
                _emit(config, "agent:log", {
                    "message": f"  Renamed {old_ref} -> {c['ref_des']} (carry-forward collision)"
                })
        else:
            num_part = ''.join(ch for ch in ref if ch.isdigit())
            if num_part:
                ref_counter[prefix] = max(ref_counter.get(prefix, 0), int(num_part))
            seen_refs.add(ref)
        seen_ids.add(id_str)

    for c in prev_selected:
        if c["id_str"] in current_ids:
            continue
        if c.get("justification", "").startswith("Auto-added by validator"):
            _claim_carried_ref(c)
            selected.append(c)
            carried += 1
            _emit(config, "agent:log", {
                "message": f"  Preserved {c['id_str']} (validator-added, carried forward)"
            })
        elif c.get("subsystem", "") in research_names:
            _claim_carried_ref(c)
            selected.append(c)
            carried += 1
            _emit(config, "agent:log", {
                "message": f"  Preserved {c['id_str']} for '{c['subsystem']}' (reranker skipped, previous selection carried forward)"
            })
    if carried:
        _emit(config, "agent:log", {"message": f"  Carried forward {carried} component(s)"})

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
            snippet = fetch_datasheet_text(url, offset=0, length=500)
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
                        chosen = {
                            "id_str": pid,
                            "category": pid.split(":")[0] if ":" in pid else "Device",
                            "text": sp.get("description", ""),
                            "footprint": "",
                            "pads": [],
                        }
                        _emit(config, "agent:log", {
                            "message": f"  Used fallback symbol {pid} for {sp['description']} (not in RAG results)"
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
                    ref_prefix = sp["ref_des_prefix"]
                    ref = _next_ref(ref_prefix)
                    injected.append({
                        "id_str": chosen["id_str"],
                        "ref_des": ref,
                        "category": chosen["id_str"].split(":")[0] if ":" in chosen["id_str"] else "Device",
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
                print(f"Support component search failed: {e}")
    if injected:
        selected.extend(injected)
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
    _emit_activity(config, "select", "Component Selection", "update", level="success", kind="selection", detail=part_names)
    _emit_activity(config, "select", "Component Selection", "done")
    return _stage_result(state, "select", {
        "selected_components": selected,
        "retry_count": retry_count + 1,
    })
