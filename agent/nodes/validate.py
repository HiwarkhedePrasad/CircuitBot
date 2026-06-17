import json

from agent.prompts import VALIDATE_SYSTEM, VALIDATE_USER
from agent.tools import search_components
from agent.utils import (
    _emit, _check_stage_contract, _stage_result, _call_llm, _clean_json, _ref_prefix_for,
)


def validate_node(state, config):
    _emit(config, "agent:thinking", {"message": "Validating component selections..."})
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
        _emit(config, "agent:log", {
            "message": f"  [{issue.get('severity', 'info').upper()}] {issue.get('message', '')}"
        })
    corrections = []
    if missing:
        _emit(config, "agent:thinking", {"message": f"Searching for {len(missing)} missing component(s)..."})
        for mc in missing:
            query = mc.get("suggested_query", mc.get("description", ""))
            try:
                results = search_components(query, k=5)
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
    if errors:
        _emit(config, "agent:log", {
            "message": f"Validation found {len(errors)} error(s) — will retry selection"
        })
    _emit(config, "agent:log", {
        "message": f"Validation done: {len(comps)} components, {len(errors)} error(s), {len(warnings)} warning(s)"
    })
    return _stage_result(state, "validate", {
        "selected_components": comps,
        "validation_errors": validation_errors,
    })
