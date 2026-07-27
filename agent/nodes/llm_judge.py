"""LLM-Judge evaluation pass — scored quality assessment of the final design.

Runs after design_review as the final quality gate. Scores the design across
multiple dimensions (completeness, correctness, connectivity, quality,
manufacturability) and provides detailed justifications.

Based on the LLM-as-judge evaluation pattern from pcbGPT (arXiv:2606.01188).
"""

import json
from agent.llm_utils import _call_llm
from agent.emit_utils import _clean_json
from agent.utils import emit_thought, emit_tool_call, emit_tool_end
from uuid import uuid4


JUDGE_SYSTEM = """You are a senior hardware engineering judge evaluating a completed PCB design.
Score the design across 5 dimensions, each 0-10, with detailed justification.

SCORING RUBRIC:

1. COMPLETENESS (0-10): Are all required components present?
   - 0-3: Major subsystems missing entirely
   - 4-6: Some subsystems present, key components absent
   - 7-8: All major subsystems present, minor parts missing
   - 9-10: All required components present, no gaps

2. CORRECTNESS (0-10): Are component selections appropriate?
   - 0-3: Wrong component types selected
   - 4-6: Suitable but suboptimal choices (overkill, wrong specs)
   - 7-8: Good choices, meets requirements
   - 9-10: Ideal selections — exactly what is needed, appropriate specs

3. CONNECTIVITY (0-10): Are all required connections present?
   - 0-3: Critical buses/wires missing
   - 4-6: Some connections present, many incomplete
   - 7-8: All major connections present, minor omissions
   - 9-10: Full connectivity, proper bus wiring

4. QUALITY (0-10): Are engineering best practices followed?
   - 0-3: No decoupling, no pull-ups, no protection
   - 4-6: Basic practices followed, gaps remain
   - 7-8: Good practices, minor improvements possible
   - 9-10: Excellent — decoupling, pull-ups, protection all addressed

5. MANUFACTURABILITY (0-10): Is the design practical to build?
   - 0-3: Through-hole parts on SMD board, unavailable packages
   - 4-6: Mixed assembly, non-preferred packages
   - 7-8: Good package choices, standard values
   - 9-10: All SMD, common packages, easily sourced parts

OVERALL: Average of all 5 scores, weighted equally.

For EACH dimension provide:
- score: int 0-10
- justification: 1-3 sentence explanation
- evidence: specific component/net references that support your score

Output ONLY JSON:
{
  "completeness": {"score": 0, "justification": "...", "evidence": "..."},
  "correctness": {"score": 0, "justification": "...", "evidence": "..."},
  "connectivity": {"score": 0, "justification": "...", "evidence": "..."},
  "quality": {"score": 0, "justification": "...", "evidence": "..."},
  "manufacturability": {"score": 0, "justification": "...", "evidence": "..."},
  "overall": 0.0,
  "summary": "2-3 sentence overall assessment",
  "critical_issues": ["issue1", "issue2"],
  "recommendations": ["rec1", "rec2"]
}

No markdown, no explanation outside the JSON."""


JUDGE_USER = """Design Prompt: {prompt}

Components ({count} total):
{components_str}

Nets ({count_nets} total):
{nets_str}

Design Review Notes:
{review_str}

Evaluate this completed design using the scoring rubric.
Be critical — score based on actual content, not intent."""


def _format_components(components: list[dict]) -> str:
    lines = []
    for c in components:
        ref = c.get("ref_des", "?")
        id_str = c.get("id_str", "?")
        desc = (c.get("description", "") or "")[:80]
        value = c.get("value", "")
        fp = c.get("footprint", "")
        v = f" ({value})" if value else ""
        f = f" [{fp}]" if fp else ""
        lines.append(f"  {ref}: {id_str}{v}{f} — {desc}")
    return "\n".join(lines)


def _format_nets(nets: list[dict]) -> str:
    lines = []
    for net in (nets or []):
        name = net.get("name", "?")
        pins = ", ".join(net.get("pins", []))
        lines.append(f"  {name}: {pins}")
    return "\n".join(lines)


def _format_review(review_suggestions: list[dict] | None) -> str:
    if not review_suggestions:
        return "No review suggestions generated."
    lines = []
    for s in review_suggestions:
        cat = s.get("category", "?")
        sev = s.get("severity", "?")
        desc = (s.get("description", "") or "")[:100]
        lines.append(f"  [{sev}] {cat}: {desc}")
    return "\n".join(lines) if lines else "No review suggestions."


def llm_judge_node(state: dict, config) -> dict:
    """Run LLM judge evaluation on the completed design."""
    judge_id = uuid4().hex[:8]
    emit_tool_call(config, judge_id, "LLM Judge", "running")
    emit_thought(config, "Evaluating design quality...")

    components = state.get("selected_components", [])
    nets = state.get("nets", [])
    prompt = state.get("prompt", "")
    review_suggestions = state.get("review_suggestions")

    context = JUDGE_USER.format(
        prompt=prompt or "(not provided)",
        count=len(components),
        components_str=_format_components(components),
        count_nets=len(nets),
        nets_str=_format_nets(nets),
        review_str=_format_review(review_suggestions),
    )

    try:
        raw = _call_llm(JUDGE_SYSTEM, context, stage="llm_judge")
        raw = _clean_json(raw)
        result = json.loads(raw) if raw else {}
    except Exception:
        result = {}

    # Validate expected keys exist
    dimensions = ["completeness", "correctness", "connectivity", "quality", "manufacturability"]
    for dim in dimensions:
        if dim not in result or not isinstance(result.get(dim), dict):
            result[dim] = {"score": 0, "justification": "Evaluation failed", "evidence": ""}

    scores = [result[dim].get("score", 0) for dim in dimensions]
    result["overall"] = round(sum(scores) / len(scores), 1) if scores else 0.0

    emit_tool_end(config, judge_id, f"Judge: overall={result['overall']}/10")

    return {"judge_evaluation": result}
