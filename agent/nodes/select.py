import json

from agent.datasheet import fetch_datasheet_text
from agent.prompts import SELECT_SYSTEM, SELECT_USER, DATASHEET_EXTEND_SYSTEM, DATASHEET_EXTEND_USER
from agent.support_rules import get_supporting_components
from agent.tools import search_components, fetch_footprint
from agent.utils import (
    _emit, _emit_activity, _check_stage_contract, _stage_result, _call_llm, _clean_json,
    _is_passive, _ref_prefix_for, MAX_VALIDATION_RETRIES,
)

_CATEGORY_FILTERS = {
    "Bulk Capacitor": {
        "reject": ["MCU", "CPLD", "FPGA", "DSP", "Antenna", "Connector", "Memory", "LED", "Sensor"],
    },
    "I2C Pull-up Resistors": {
        "reject": ["MCU", "Sensor", "Antenna", "Connector", "CPLD", "Memory", "LED"],
    },
    "Status LED": {
        "reject": ["CPLD", "FPGA", "MCU", "Memory", "Antenna", "Connector", "Sensor"],
        "reject_keywords": ["infrared", "950nm", "940nm", "ir led", "ir emitter"],
    },
    "Status Indicator": {
        "reject": ["CPLD", "FPGA", "MCU", "Memory", "Antenna", "Connector", "Sensor"],
        "reject_keywords": ["infrared", "950nm", "940nm", "ir led", "ir emitter"],
    },
    "Crystal Oscillator": {
        "allow": ["Crystal", "Oscillator"],
        "reject": ["MCU", "CPLD", "Sensor", "Memory"],
    },
    "Decoupling Capacitors": {
        "reject": ["MCU", "CPLD", "FPGA", "Antenna", "Connector", "LED", "Sensor", "Memory"],
    },
    "USB-C Power Input": {
        "allow": ["Connector"],
        "reject": ["Interface_USB"],
    },
    "Power Regulation": {
        "allow": ["Regulator"],
        "reject": ["MCU", "CPLD", "Sensor", "LED", "Antenna", "Display"],
    },
    "USB Interface": {
        "allow": ["Connector"],
        "reject": ["Interface_USB"],
    },
    "Passive Components": {
        "reject": ["MCU", "CPLD", "FPGA", "DSP", "Antenna", "Connector", "Memory", "LED", "Sensor", "Regulator", "Display"],
        "reject_keywords": ["ohmmeter", "ammeter", "voltmeter", "meter", "galvanometer"],
    },
}


def _filter_by_category(subsystem_name: str, candidates: list[dict]) -> list[dict]:
    rules = _CATEGORY_FILTERS.get(subsystem_name)
    if not rules:
        return candidates
    kept = []
    reject_keywords = rules.get("reject_keywords", [])
    for c in candidates:
        cat = (c.get("category") or c["id_str"].split(":")[0]).upper()
        cid = c["id_str"]
        allow = rules.get("allow")
        reject = rules.get("reject", [])
        if allow and not any(a.upper() in cat for a in allow):
            print(f"  Filtered out [{subsystem_name}]: {cid} (category={cat})")
            continue
        if reject and any(r.upper() in cat for r in reject):
            print(f"  Filtered out [{subsystem_name}]: {cid} (category={cat})")
            continue
        if reject_keywords:
            text = (c.get("description") or c.get("text") or "").lower()
            matched_kw = None
            for kw in reject_keywords:
                if kw in text:
                    matched_kw = kw
                    break
            if matched_kw:
                print(f"  Filtered out [{subsystem_name}]: {cid} (keyword={matched_kw})")
                continue
        kept.append(c)
    print(f"  Category filter [{subsystem_name}]: {len(kept)}/{len(candidates)} kept")
    return kept


def select_node(state, config):
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})
    _emit_activity(config, "select", "Component Selection", "start")
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

    filtered_research = []
    for sub in research:
        fsub = sub.copy()
        fsub["results"] = _filter_by_category(sub.get("subsystem", ""), sub.get("results", []))
        filtered_research.append(fsub)

    rejected_ids = set(state.get("rejected_ids", []))
    if rejected_ids:
        for sub in filtered_research:
            before = len(sub["results"])
            sub["results"] = [r for r in sub["results"] if r["id_str"] not in rejected_ids]
            dropped = before - len(sub["results"])
            if dropped:
                _emit(config, "agent:log", {
                    "message": f"  Rejected {dropped} previously-failed component(s) for '{sub.get('subsystem', '')}'"
                })

    results_json = json.dumps(filtered_research, indent=2)
    if len(results_json) > 8000:
        truncated = []
        for sub in filtered_research:
            tsub = sub.copy()
            tsub["results"] = []
            for r in sub.get("results", []):
                tr = r.copy()
                if len(tr.get("text", "")) > 100:
                    tr["text"] = tr["text"][:97] + "..."
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
        if not selected:
            try:
                broad = search_components(state.get("prompt", "electronic component"), k=5)
                for i, r in enumerate(broad):
                    selected.append({
                        "id_str": r["id_str"],
                        "ref_des": f"U{i+1}",
                        "category": r["id_str"].split(":")[0] if ":" in r["id_str"] else "General",
                        "description": r.get("text", ""),
                        "justification": "Broad-search emergency fallback",
                        "datasheet_text": "",
                    })
            except Exception:
                pass
    else:
        valid_ids = set()
        for sub in filtered_research:
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
                        best = results[0]
                        filtered.append({
                            "id_str": best["id_str"],
                            "ref_des": s["ref_des"],
                            "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                            "description": best.get("text", ""),
                            "justification": s.get("justification", f"Fallback for rejected {s['id_str']}"),
                            "datasheet_text": best.get("datasheet_snippet", ""),
                        })
                        break
                else:
                    try:
                        broad = search_components(s.get("description", s["id_str"]), k=3)
                        if broad:
                            best = broad[0]
                            filtered.append({
                                "id_str": best["id_str"],
                                "ref_des": s["ref_des"],
                                "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                                "description": best.get("text", ""),
                                "justification": f"Broad-search fallback for {s['id_str']}",
                                "datasheet_text": best.get("datasheet_snippet", ""),
                            })
                    except Exception:
                        pass
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

    for s in selected:
        id_str = s["id_str"]
        cat = (s.get("category", "") or "").upper()
        desc = (s.get("description", "") or "").upper()
        needs_usb_swap = (
            cat.startswith("INTERFACE_USB")
            or "FUSB" in id_str.upper()
            or not (cat.startswith("CONNECTOR") and "USB" in cat)
        )
        for sub in filtered_research:
            sname = sub.get("subsystem", "").upper()
            if "USB" in sname and id_str in {r["id_str"] for r in sub.get("results", [])}:
                needs_usb_swap = True
                break
        if needs_usb_swap:
            query = "USB-C connector" if ("USB-C" in desc or "TYPE-C" in desc) else "USB connector"
            try:
                for r in search_components(query, k=6):
                    r_cat = (r.get("category", "") or "").upper()
                    if r_cat.startswith("CONNECTOR") and "USB" in r_cat:
                        old = s["id_str"]
                        s["id_str"] = r["id_str"]
                        s["category"] = r.get("category", s["category"])
                        s["description"] = r.get("text", s.get("description", ""))
                        _emit(config, "agent:log", {
                            "message": f"  Swapped {old} -> {s['id_str']} (real USB connector)"
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
    part_names = [f'{s["ref_des"]}={s["id_str"].split(":")[-1]}' for s in selected if s.get("id_str")]
    _emit_activity(config, "select", "Component Selection", "update", level="success", kind="selection", detail=part_names)
    _emit_activity(config, "select", "Component Selection", "done")
    return _stage_result(state, "select", {
        "selected_components": selected,
        "retry_count": retry_count + 1,
    })
