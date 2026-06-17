import json

from agent.datasheet import fetch_datasheet_text
from agent.prompts import SELECT_SYSTEM, SELECT_USER, DATASHEET_EXTEND_SYSTEM, DATASHEET_EXTEND_USER
from agent.support_rules import get_supporting_components
from agent.tools import search_components, fetch_footprint
from agent.utils import (
    _emit, _check_stage_contract, _stage_result, _call_llm, _clean_json,
    _is_passive, _ref_prefix_for, MAX_VALIDATION_RETRIES,
)


def select_node(state, config):
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})
    contract = _check_stage_contract("select", state, ["research_results", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "select", {"selected_components": []})
    retry_count = state.get("retry_count", 0)
    validation_errors = state.get("validation_errors", [])
    research = state.get("research_results", [])
    if not research:
        _emit(config, "agent:log", {"message": "No research results to select from."})
        return _stage_result(state, "select", {"selected_components": []})

    _emit(config, "agent:thinking", {"message": "Fetching datasheets for top candidates..."})
    for sub in research:
        for r in sub.get("results", [])[:2]:
            url = r.get("datasheet", "")
            if url:
                snippet = fetch_datasheet_text(url, offset=0, length=500)
                r["datasheet_snippet"] = snippet
                if snippet:
                    _emit(config, "agent:log", {
                        "message": f"  Fetched datasheet ({len(snippet)} chars) for {r['id_str']}"
                    })
            else:
                r["datasheet_snippet"] = ""

    results_json = json.dumps(research, indent=2)
    if len(results_json) > 8000:
        truncated = []
        for sub in research:
            tsub = sub.copy()
            tsub["results"] = []
            for r in sub.get("results", []):
                tr = r.copy()
                if len(tr.get("text", "")) > 100:
                    tr["text"] = tr["text"][:97] + "..."
                if len(tr.get("datasheet_snippet", "")) > 200:
                    tr["datasheet_snippet"] = tr["datasheet_snippet"][:197] + "..."
                tsub["results"].append(tr)
            truncated.append(tsub)
        results_json = json.dumps(truncated, indent=2)

    select_user_prompt = SELECT_USER.format(
        prompt=state["prompt"], results_json=results_json
    )
    if validation_errors and retry_count > 0:
        feedback = "\n\nPREVIOUS VALIDATION ISSUES (fix these):\n" + "\n".join(
            f"- {e}" for e in validation_errors[:5]
        )
        select_user_prompt += feedback
        _emit(config, "agent:log", {
            "message": f"Re-selecting with {len(validation_errors)} validation error(s) as feedback (retry {retry_count + 1}/{MAX_VALIDATION_RETRIES})"
        })

    try:
        text = _call_llm(SELECT_SYSTEM, select_user_prompt, stage="select")
    except Exception:
        text = ""
    text = _clean_json(text)
    try:
        selected = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse selection JSON: {text[:200]}")
        selected = []

    needs_more = [s for s in selected if s.get("need_more_datasheet")]
    if needs_more:
        _emit(config, "agent:thinking", {"message": f"Extending datasheet for {len(needs_more)} component(s)..."})
        for s in needs_more:
            id_str = s["id_str"]
            for sub in research:
                for r in sub.get("results", []):
                    if r["id_str"] == id_str:
                        url = r.get("datasheet", "")
                        if url:
                            extra = fetch_datasheet_text(url, offset=500, length=500)
                            if extra:
                                try:
                                    ext_text = _call_llm(
                                        DATASHEET_EXTEND_SYSTEM,
                                        DATASHEET_EXTEND_USER.format(
                                            id_str=id_str,
                                            description=s.get("description", ""),
                                            extended_text=extra,
                                        ), stage="datasheet_extend"
                                    )
                                except Exception:
                                    continue
                                ext_clean = _clean_json(ext_text)
                                try:
                                    ext_result = json.loads(ext_clean) if ext_clean else {}
                                    if not ext_result.get("suitable", True):
                                        _emit(config, "agent:log", {
                                            "message": f"  Datasheet check: {id_str} marked unsuitable: {ext_result.get('justification', '')}"
                                        })
                                except json.JSONDecodeError:
                                    pass
                        break
        for s in selected:
            s.pop("need_more_datasheet", None)

    if not selected:
        ref_letters = "URCL"
        selected = []
        for i, sub in enumerate(research):
            results = sub.get("results", [])
            if results:
                best = results[0]
                ref = f"{ref_letters[i % len(ref_letters)]}{i + 1}"
                selected.append({
                    "id_str": best["id_str"],
                    "ref_des": ref,
                    "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                    "description": best.get("text", ""),
                    "justification": "Fallback: first available component for subsystem",
                    "datasheet_text": best.get("datasheet_snippet", ""),
                })
    else:
        valid_ids = set()
        for sub in research:
            for r in sub.get("results", []):
                valid_ids.add(r["id_str"])
        filtered = []
        for s in selected:
            if s["id_str"] in valid_ids:
                filtered.append(s)
            else:
                _emit(config, "agent:log", {"message": f"  Rejected hallucinated ID: {s['id_str']}"})
                for sub in research:
                    results = sub.get("results", [])
                    if results:
                        filtered.append({
                            "id_str": results[0]["id_str"],
                            "ref_des": s["ref_des"],
                            "category": results[0]["id_str"].split(":")[0] if ":" in results[0]["id_str"] else "General",
                            "description": results[0].get("text", ""),
                            "justification": s.get("justification", f"Fallback for rejected {s['id_str']}"),
                            "datasheet_text": results[0].get("datasheet_snippet", ""),
                        })
                        break
        selected = filtered

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

    for s in selected:
        id_str = s["id_str"]
        cat = (s.get("category", "") or "").upper()
        desc = (s.get("description", "") or "").upper()
        if cat.startswith("INTERFACE_USB") or "FUSB" in id_str.upper():
            query = "USB-C connector" if "USB-C" in desc or "TYPE-C" in desc else "USB connector"
            try:
                for r in search_components(query, k=6):
                    r_cat = (r.get("category", "") or "").upper()
                    if r_cat.startswith("CONNECTOR") and "USB" in r_cat:
                        old = s["id_str"]
                        s["id_str"] = r["id_str"]
                        s["category"] = r.get("category", s["category"])
                        s["description"] = r.get("text", s.get("description", ""))
                        _emit(config, "agent:log", {
                            "message": f"  Swapped {old} -> {s['id_str']} (real connector)"
                        })
                        break
            except Exception as e:
                print(f"Connector swap failed: {e}")

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
    return _stage_result(state, "select", {
        "selected_components": selected,
        "retry_count": retry_count + 1,
    })
