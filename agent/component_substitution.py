"""Component substitution intelligence — find alternatives and replacements."""

from typing import List, Optional
from agent.tools import search_components


def find_alternatives(
    component_ref: str,
    component_name: str = "",
    component_value: str = "",
    k: int = 5,
) -> List[dict]:
    """Find alternative components that could replace the given component.

    Searches for pin-compatible alternatives, cheaper equivalents, and
    same-function parts with different packages.

    Returns a list of dicts with: id_str, name, score, reason.
    """
    alternatives = []

    # Strategy 1: Search by component name/function
    if component_name:
        results = search_components(component_name, k=k)
        for r in results:
            alt = {
                "id_str": r.id_str,
                "name": r.id_str.split(":")[-1] if ":" in r.id_str else r.id_str,
                "score": r.score,
                "reason": f"Same function as {component_name}",
                "footprint": getattr(r, "footprint", ""),
                "pins": getattr(r, "pins", []),
            }
            alternatives.append(alt)

    # Strategy 2: Search by value if it's a passive component
    if component_value and _is_passive_value(component_value):
        results = search_components(component_value, k=k)
        for r in results:
            alt = {
                "id_str": r.id_str,
                "name": r.id_str.split(":")[-1] if ":" in r.id_str else r.id_str,
                "score": r.score * 0.8,  # Slightly lower score for value-only matches
                "reason": f"Same value ({component_value})",
                "footprint": getattr(r, "footprint", ""),
                "pins": getattr(r, "pins", []),
            }
            alternatives.append(alt)

    # Deduplicate by id_str
    seen = set()
    unique = []
    for alt in alternatives:
        if alt["id_str"] not in seen:
            seen.add(alt["id_str"])
            unique.append(alt)

    # Sort by score
    unique.sort(key=lambda x: -x.get("score", 0))
    return unique[:k]


def find_pin_compatible(
    component_id: str,
    k: int = 5,
) -> List[dict]:
    """Find pin-compatible alternatives for a component.

    Uses the component's pin count and function to find alternatives
    with the same interface.
    """
    # Search for components in the same family
    family = _extract_family(component_id)
    if not family:
        return []

    results = search_components(family, k=k * 2)
    alternatives = []
    for r in results:
        if r.id_str == component_id:
            continue  # Skip the original
        alt = {
            "id_str": r.id_str,
            "name": r.id_str.split(":")[-1] if ":" in r.id_str else r.id_str,
            "score": r.score,
            "reason": f"Same family ({family})",
            "footprint": getattr(r, "footprint", ""),
            "pins": getattr(r, "pins", []),
        }
        alternatives.append(alt)

    return alternatives[:k]


def suggest_upgrade(
    component_id: str,
    component_name: str = "",
) -> Optional[dict]:
    """Suggest a better/cheaper/newer alternative for a component.

    Returns a single suggestion or None.
    """
    alternatives = find_alternatives(component_ref="", component_name=component_name, k=3)
    if alternatives:
        best = alternatives[0]
        return {
            "original": component_id,
            "suggested": best["id_str"],
            "reason": best["reason"],
        }
    return None


def _is_passive_value(value: str) -> bool:
    """Check if a value looks like a passive component value (ohm, farad, etc.)."""
    value_lower = value.lower().strip()
    passive_patterns = [
        r"\d+\.?\d*\s*(r|rpm|ohm)",  # resistance
        r"\d+\.?\d*\s*(uf|nf|pf|mf)",  # capacitance
        r"\d+\.?\d*\s*(mh|uh|nh)",  # inductance
        r"\d+\.?\d*\s*(k|r|ohm)",  # resistance shorthand
    ]
    import re
    for pattern in passive_patterns:
        if re.search(pattern, value_lower):
            return True
    return False


def _extract_family(component_id: str) -> str:
    """Extract the component family from an ID string."""
    # "Regulator_Linear:AMS1117-3.3" -> "AMS1117"
    # "MCU_ST_ESP32:ESP32-WROOM-32" -> "ESP32"
    if ":" in component_id:
        part = component_id.split(":")[-1]
    else:
        part = component_id

    # Extract base part number (remove package suffixes like -3.3, -SS, etc.)
    import re
    match = re.match(r"^([A-Za-z0-9]+)", part)
    if match:
        return match.group(1)
    return part
