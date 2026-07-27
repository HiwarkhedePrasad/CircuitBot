"""Design review node — proactive suggestions after design completion.

Uses the Runtime's ContextEngine for structured context and MemoryService
for recording decisions and learning from past reviews.
"""

import json
from agent.llm_utils import _call_llm
from agent.emit_utils import _clean_json
from agent.utils import emit_thought, emit_tool_call, emit_tool_end
from agent.runtime import get_runtime
from agent.runtime.context_engine import WorkScope
from uuid import uuid4


REVIEW_SYSTEM = """You are a senior hardware engineer reviewing a circuit design.
Analyze the design and suggest improvements. Focus on these critical checks:

POWER:
- Missing bypass/decoupling capacitors (every IC needs 100nF per power pin)
- Bulk capacitance missing on voltage rails (10-47µF electrolytic/tantalum)
- Power budget: regulator must supply 1.5-2x total IC current draw
- Reverse polarity protection on barrel jack/battery input
- Missing input/output caps on voltage regulators

SIGNAL:
- I2C buses MUST have 4.7kΩ pull-ups on 3.3V (check all I2C devices)
- USB-C CC pins need 5.1kΩ pull-downs for UFP mode
- DS18B20 and 1-Wire devices need 4.7kΩ pull-up on data line
- Crystal load caps must match crystal spec (typically 18pF)
- Level shifting needed between 5V and 3.3V domains

PROTECTION:
- USB ports need ESD protection (TPD6S300A, USBLC6-2SC6, etc.)
- Power input needs polyfuse or current limiting
- Inductive loads (motors, relays) need flyback diodes

COST:
- Can multiple regulators be consolidated?
- Unnecessary USB-UART bridge when MCU has native USB
- Package overkill (D2PAK for <500mA circuit)

LAYOUT:
- Decoupling caps must be close to IC power pins
- USB D+/D- need controlled impedance routing (~90Ω)

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
    datasheet_results = state.get("datasheet_search_results", [])

    # Try to use ContextEngine for structured context
    runtime = get_runtime(config)
    design_context = _build_design_context(
        runtime, components, nets, prompt, datasheet_results, state
    )

    try:
        raw = _call_llm(REVIEW_SYSTEM, design_context, stage="design_review")
        raw = _clean_json(raw)
        result = json.loads(raw) if raw else {}
    except Exception:
        result = {}

    suggestions = result.get("suggestions", [])
    if not isinstance(suggestions, list):
        suggestions = []

    # Record high-severity suggestions as design decisions
    if runtime and suggestions:
        _record_review_decisions(runtime, suggestions)

    if suggestions:
        emit_tool_end(config, review_id, f"Design review: {len(suggestions)} suggestion(s)")
    else:
        emit_tool_end(config, review_id, "Design review complete — no suggestions")

    return {"review_suggestions": suggestions}


def _build_design_context(runtime, components, nets, prompt, datasheet_results, state):
    """Build design context using ContextEngine when available, fallback to manual."""
    parts = []

    # Use ContextEngine for structured context if runtime is available
    if runtime:
        try:
            scope = WorkScope(
                stage="design_review",
                component_refs=[c.get("ref_des", "") for c in components[:20]],
                include_research=True,
            )
            ctx = runtime.context.build(
                design_id=runtime.design_id,
                revision=runtime.revision,
                scope=scope,
                budget=12000,  # Larger budget for review
            )
            parts.append(f"Design Intent: {prompt}")
            if ctx.design_summary:
                parts.append(f"Design Summary: {ctx.design_summary}")
            if ctx.entities:
                parts.append("\nKey Components:")
                for ref, entity in list(ctx.entities.items())[:15]:
                    parts.append(f"  {ref}: {entity.get('id_str', '?')} — {entity.get('description', '')[:80]}")
            if ctx.constraints:
                parts.append(f"\nConstraints: {len(ctx.constraints)} active")

            # Get learned patterns from memory for cross-design context
            try:
                learned = runtime.memory.get_learned_patterns(min_frequency=2)
                if learned:
                    parts.append("\nLearned Patterns from Previous Designs (pay special attention):")
                    for pattern in learned[:5]:
                        acceptance = pattern.get("accepted_count", 0)
                        total = pattern.get("accepted_count", 0) + pattern.get("rejected_count", 0)
                        rate = acceptance / total if total > 0 else 0.5
                        parts.append(f"  - {pattern.get('type', '?')}: appeared {pattern.get('frequency', 0)} times, "
                                   f"accepted {rate:.0%} of the time")

                # Get review stats for context
                stats = runtime.memory.get_review_stats()
                if stats["total"] > 0:
                    parts.append(f"\nReview History: {stats['accepted']}/{stats['total']} suggestions accepted "
                               f"({stats['acceptance_rate']:.0%} acceptance rate)")
            except Exception:
                pass
        except Exception:
            # Fallback to manual context building
            parts.extend(_manual_context(components, nets, prompt))
    else:
        parts.extend(_manual_context(components, nets, prompt))

    # Add netlist details
    parts.append("\nNets:")
    for net in nets:
        pins = ", ".join(net.get("pins", []))
        parts.append(f"  {net.get('name', '?')}: {pins}")

    # Add datasheet research for spec validation
    if datasheet_results:
        parts.append("\nDatasheet Research (use for spec validation):")
        for ds in datasheet_results[:10]:
            ref = ds.get("ref_des", "?")
            summary = (ds.get("summary", "") or "")[:400]
            if summary:
                parts.append(f"  {ref}: {summary}")

    return "\n".join(parts)


def _manual_context(components, nets, prompt):
    """Manual context building fallback."""
    parts = [f"Design intent: {prompt}", "\nComponents:"]
    for comp in components:
        ref = comp.get("ref", comp.get("ref_des", "?"))
        name = comp.get("name", "?")
        value = comp.get("value", "?")
        fp = comp.get("footprint", "?")
        parts.append(f"  {ref}: {name} ({value}) [{fp}]")
    return parts


def _record_review_decisions(runtime, suggestions):
    """Record high-severity review suggestions as design decisions.

    Also records the suggestion in memory for cross-design learning.
    """
    try:
        for idx, suggestion in enumerate(suggestions):
            if suggestion.get("severity") == "high":
                target = suggestion.get("target", {})
                entity_id = ""
                if isinstance(target, dict):
                    entity_id = target.get("ref", target.get("net", ""))
                runtime.memory.record_decision(
                    entity_id=entity_id,
                    decision=f"Review: {suggestion.get('description', '')}",
                    rationale=suggestion.get("suggestion", ""),
                    revision=runtime.revision,
                )

            # Record suggestion type for pattern learning
            category = suggestion.get("category", "")
            if category:
                # Generate a stable ID for this suggestion
                suggestion_id = f"{runtime.design_id}:{category}:{idx}"
                # Initially assume high-severity suggestions are useful
                # (user will confirm/reject via feedback)
                if suggestion.get("severity") == "high":
                    runtime.memory.record_review_feedback(
                        suggestion_id=suggestion_id,
                        accepted=True,
                        suggestion_type=category,
                    )
    except Exception:
        pass  # best-effort persistence
