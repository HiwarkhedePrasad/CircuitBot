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
        for p in parts:
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
