import json

from agent.utils import _emit, _call_llm, _clean_json

RERANK_SYSTEM = """You are a component selection engineer. Score each candidate component on how well it fits the subsystem requirement.

For each candidate, output a score 0-10:
- 0-3: Wrong type or completely unsuitable (e.g., Ohmmeter where resistor needed, USB PD controller where connector needed)
- 4-6: Somewhat suitable but suboptimal (wrong specs, overkill, bad package)
- 7-8: Good fit, meets requirements
- 9-10: Ideal fit — exactly what is needed

Scoring rules:
1. Component TYPE must match subsystem function (resistor for current limiting, LED for indication, connector for USB power)
2. Check the datasheet snippet or description to confirm suitability
3. Library prefix should match expected role: Device for passives, Connector for connectors, Sensor_* for sensors, Regulator_* for regulators
4. MODULE AWARENESS: If a development board or module that was already selected covers this subsystem's function (e.g., WEMOS_C3_mini module has on-board USB and voltage regulation), output score 0 and set justification to "SKIPPED - integrated into module"
5. The "has_footprint" field shows if the symbol has an associated PCB footprint — prefer candidates that do
6. PHYSICAL INTERFACE RULE: If the subsystem describes a physical connection to the outside world (e.g., "USB-C Power Input", "USB Interface", "Audio Jack", "Power Terminal"), the primary component MUST be a physical connector from the 'Connector_*' library. Protection ICs, ESD diodes, or PD controllers are supporting components — they must NOT be selected as the primary component. Score any non-connector primary component 0-2 for such subsystems.

COMMON COMPONENT CHEAT SHEET:
Use EXACTLY these KiCad symbols for generic supporting parts:
- Resistors: "Device:R_Small"
- Capacitors: "Device:C_Small"
- Generic LEDs: "Device:LED"
- Inductors: "Device:L_Small"
- USB-C Connectors: "Connector_USB:USB_C_Receptacle_USB2.0"
- Diodes: "Device:D_Small"

Output ONLY a JSON array of objects:
[{"id_str": "Device:R_Small", "score": 9, "justification": "Standard resistor, ideal for current limiting"}, ...]

No markdown, no explanation, just the array."""

RERANK_USER = """Subsystem: {subsystem_name}
Function: {subsystem_function}

Components already selected (check these to avoid redundancy):
{existing_str}

Candidates for this subsystem:
{candidates_json}

Score each candidate on fitness for this subsystem.
Output ONLY a JSON array — no markdown."""


def rank_candidates(
    subsystem: dict,
    candidates: list[dict],
    existing_components: list[dict] | None = None,
    config=None,
) -> list[dict]:
    if not candidates:
        return []

    subsystem_name = subsystem.get("subsystem", "unknown")
    subsystem_function = subsystem.get("function", "")

    existing_str = ""
    if existing_components:
        existing_str = "\n".join(
            f'  {c["ref_des"]}: {c["id_str"]} - {c.get("description", "")[:60]}'
            for c in existing_components
        )

    compact = []
    for c in candidates:
        desc = (c.get("text") or c.get("description") or "")[:200]
        ds = (c.get("datasheet_snippet") or "")[:300]
        compact.append({
            "id_str": c["id_str"],
            "category": c.get("category", c["id_str"].split(":")[0]),
            "description": desc,
            "datasheet_snippet": ds,
            "has_footprint": bool(c.get("footprint")),
        })

    user_prompt = RERANK_USER.format(
        subsystem_name=subsystem_name,
        subsystem_function=subsystem_function,
        existing_str=existing_str or "None yet",
        candidates_json=json.dumps(compact, indent=2),
    )

    try:
        text = _call_llm(RERANK_SYSTEM, user_prompt, stage="rerank")
    except Exception:
        text = ""

    text = _clean_json(text)
    try:
        scored = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Reranker parse failed for '{subsystem_name}': {text[:100]}")
        scored = []

    score_map = {s["id_str"]: s for s in scored if isinstance(s, dict) and "id_str" in s}
    for c in candidates:
        s = score_map.get(c["id_str"], {})
        c["score"] = s.get("score", 0)
        c["justification"] = s.get("justification", "")

    candidates.sort(key=lambda c: c.get("score", 0), reverse=True)

    if config:
        _emit(config, "agent:log", {
            "message": f"  Reranked [{subsystem_name}]: top={candidates[0]['id_str']} "
                       f"(score={candidates[0].get('score', 0)})" if candidates else f"  Reranked [{subsystem_name}]: no candidates"
        })
        for c in candidates[:3]:
            _emit(config, "agent:log", {
                "message": f"    {c['id_str']}: score={c.get('score', 0)} — {c.get('justification', '')[:80]}"
            })

    return candidates
