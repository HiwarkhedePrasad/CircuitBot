import json

from agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER
from agent.utils import _emit, _check_stage_contract, _stage_result, _call_llm, _clean_json


def analyze_node(state, config):
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
    contract = _check_stage_contract("analyze", state, ["prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "analyze", {"analysis": []})
    try:
        text = _call_llm(ANALYZE_SYSTEM, ANALYZE_USER.format(prompt=state["prompt"]), stage="analyze")
    except Exception:
        text = ""
    text = _clean_json(text)
    try:
        analysis = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse analysis JSON: {text[:200]}")
        analysis = []
    if not analysis:
        analysis = [{"subsystem": state["prompt"], "function": "Main function", "example_components": []}]
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " +
                   ", ".join(a.get("subsystem", "?") for a in analysis)
    })
    return _stage_result(state, "analyze", {"analysis": analysis})
