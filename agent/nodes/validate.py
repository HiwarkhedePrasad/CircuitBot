import json

from agent.prompts import VALIDATE_SYSTEM, VALIDATE_USER
from agent.tools import search_components
from agent.utils import (
    _emit, _emit_activity, _check_stage_contract, _stage_result, _call_llm, _clean_json, _ref_prefix_for,
    MAX_VALIDATION_RETRIES,
)

_CRITICAL_PATTERNS = [
    ("infrared", "led", "Status LED is infrared — not visible to human eye"),
    ("antenna", "resistor", "Antenna selected where resistor required"),
    ("cpld", "capacitor", "CPLD selected where capacitor required"),
    ("pd controller", "connector", "USB PD controller selected where USB-C connector required"),
]


def validate_node(state, config):
    _emit(config, "agent:thinking", {"message": "Validating component selections..."})
    _emit_activity(config, "validate", "Validation", "start")
    contract = _check_stage_contract("validate", state, ["selected_components", "analysis", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "validate", {"selected_components": [], "validation_errors": []})
    comps = state.get("selected_components", [])
    analysis = state.get("analysis", [])
    prompt = state.get("prompt", "")
    if not comps:
        _emit(config, "agent:log", {"message": "No components to validate."})
        return _stage_result(state, "validate", {"selected_components": comps, "validation_errors": []})
    components_list = "\n".join(
        f'  {c["ref_des"]}: {c["id_str"]}  [{c.get("category", "?")}]  "{c.get("description", "")[:80]}"'
        for c in comps
    )
    subsystems = "\n".join(
        f'  {a.get("subsystem", "?")}: {a.get("function", "")}'
        for a in analysis
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
    issues = result.get("issues", [])
    missing = result.get("missing_components", [])
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        for keyword, context, reason in _CRITICAL_PATTERNS:
            if keyword in msg and context in msg:
                detail = (f"Critical validation failure: {reason}\n"
                          f"  Component: {issue.get('id_str', '?')}\n"
                          f"  Detail: {issue.get('message', '')}\n"
                          f"  Suggestion: {issue.get('suggestion', '')}")
                rejected = list(state.get("rejected_ids", []))
                if issue.get("id_str") and issue["id_str"] not in rejected:
                    rejected.append(issue["id_str"])
                _emit_activity(config, "validate", "Validation", "update", level="error", kind="validation", detail=detail)
                _emit_activity(config, "validate", "Validation", "done")
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
        _emit_activity(config, "validate", "Validation", "update", level="error", kind="validation", detail=err.get("message", ""))
    for w in warnings:
        _emit_activity(config, "validate", "Validation", "update", level="warning", kind="validation", detail=w.get("message", ""))
    corrections = []
    if missing:
        _emit(config, "agent:thinking", {"message": f"Searching for {len(missing)} missing component(s)..."})
        for mc in missing:
            query = mc.get("suggested_query", mc.get("description", ""))
            try:
                lib_filter = mc.get("library_filter") or None
                results = search_components(query, k=5, library_filter=lib_filter)
                if results:
                    best = results[0]
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
    validation_errors = [e["message"] for e in errors]
    rejected = list(state.get("rejected_ids", []))
    for e in errors:
        eid = e.get("id_str", "")
        if eid and eid not in rejected:
            rejected.append(eid)
    if errors:
        _emit(config, "agent:log", {
            "message": f"Validation found {len(errors)} error(s) — will retry selection"
        })
    else:
        _emit_activity(config, "validate", "Validation", "update", level="success", kind="validation", detail="Validation passed")
    _emit(config, "agent:log", {
        "message": f"Validation done: {len(comps)} components, {len(errors)} error(s), {len(warnings)} warning(s)"
    })
    _emit_activity(config, "validate", "Validation", "done")
    result = {
        "selected_components": comps,
        "validation_errors": validation_errors,
        "rejected_ids": rejected,
    }
    if errors and state.get("retry_count", 0) >= MAX_VALIDATION_RETRIES:
        error_msgs = "; ".join(validation_errors[:3])
        result["error"] = f"Validation failed after {MAX_VALIDATION_RETRIES} retries: {error_msgs}"
    return _stage_result(state, "validate", result)
