"""Design review node — proactive suggestions after design completion."""

import json
from agent.llm_utils import _call_llm
from agent.emit_utils import _clean_json
from agent.utils import emit_thought, emit_tool_call, emit_tool_end
from uuid import uuid4


REVIEW_SYSTEM = """You are a senior hardware engineer reviewing a circuit design.
Analyze the design and suggest improvements. Focus on:

1. POWER: Missing bypass/decoupling capacitors, power budget issues
2. SIGNAL: Missing pull-ups/pull-downs, signal integrity
3. PROTECTION: ESD protection, reverse polarity, current limiting
4. COST: Part consolidation, cheaper alternatives
5. LAYOUT: Component placement hints, trace routing suggestions

Return JSON:
{
  "suggestions": [
    {
      "category": "power" | "signal" | "protection" | "cost" | "layout",
      "severity": "high" | "medium" | "low",
      "description": "Clear description of the issue",
      "suggestion": "Actionable suggestion to fix it",
      "target": {"ref": "U1"} or {"net": "VCC"} or null
    }
  ]
}

Be concise. Only suggest issues that matter. Max 5 suggestions.
If the design looks good, return {"suggestions": []}.

Return ONLY the JSON object."""


def design_review_node(state: dict, config) -> dict:
    """Run design review and generate suggestions."""
    review_id = uuid4().hex[:8]
    emit_tool_call(config, review_id, "Design Review", "running")
    emit_thought(config, "Reviewing the completed design...")

    components = state.get("selected_components", [])
    nets = state.get("nets", [])
    prompt = state.get("prompt", "")

    # Build design context
    design_context = f"Design intent: {prompt}\n\nComponents:\n"
    for comp in components:
        ref = comp.get("ref", comp.get("ref_des", "?"))
        name = comp.get("name", "?")
        value = comp.get("value", "?")
        fp = comp.get("footprint", "?")
        design_context += f"- {ref}: {name} ({value}) [{fp}]\n"

    design_context += "\nNets:\n"
    for net in nets:
        pins = ", ".join(net.get("pins", []))
        design_context += f"- {net.get('name', '?')}: {pins}\n"

    try:
        raw = _call_llm(REVIEW_SYSTEM, design_context, stage="design_review")
        raw = _clean_json(raw)
        result = json.loads(raw) if raw else {}
    except Exception:
        result = {}

    suggestions = result.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []

    if suggestions:
        emit_tool_end(config, review_id, f"Design review: {len(suggestions)} suggestion(s)")
    else:
        emit_tool_end(config, review_id, "Design review complete — no suggestions")

    return {"review_suggestions": suggestions}
