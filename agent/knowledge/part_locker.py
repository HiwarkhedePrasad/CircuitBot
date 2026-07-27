"""Immutable Requirement & Part Locker for CircuitBot.

Extracts explicitly named part numbers and circuit specifications from user prompts
and locks them as immutable components (is_user_locked = True). Locked components
cannot be removed, substituted, or scored down by LLM rerankers or repair nodes.
"""

from __future__ import annotations
import logging
from typing import Any
from agent.utils import _extract_part_numbers
from agent.knowledge.fuzzy_matcher import fuzzy_fallback, fuzzy_search_exact
from agent.state_models import ComponentModel, make_functional_id

logger = logging.getLogger(__name__)


# Standard mappings for common user-specified part names to KiCad symbols
_KNOWN_USER_PARTS: dict[str, dict[str, str]] = {
    "LM35": {
        "id_str": "Sensor_Temperature:LM35-LP",
        "category": "Sensor_Temperature",
        "description": "LM35 precision centigrade temperature sensor in TO-92",
    },
    "LM35DZ": {
        "id_str": "Sensor_Temperature:LM35-LP",
        "category": "Sensor_Temperature",
        "description": "LM35DZ precision centigrade temperature sensor in TO-92",
    },
    "AMS1117-3.3": {
        "id_str": "Regulator_Linear:AMS1117-3.3",
        "category": "Regulator_Linear",
        "description": "AMS1117-3.3 1A LDO regulator in SOT-223",
        "value": "3.3V",
    },
    "AMS1117-3": {
        "id_str": "Regulator_Linear:AMS1117-3.3",
        "category": "Regulator_Linear",
        "description": "AMS1117-3.3 1A LDO regulator in SOT-223",
        "value": "3.3V",
    },
    "ESP32-C3-WROOM-02": {
        "id_str": "RF_Module:ESP32-C3-WROOM-02",
        "category": "RF_Module",
        "description": "ESP32-C3-WROOM-02 Wi-Fi + BLE 5.0 module with PCB antenna",
    },
    "ESP32-C3-WROOM-02U": {
        "id_str": "RF_Module:ESP32-C3-WROOM-02U",
        "category": "RF_Module",
        "description": "ESP32-C3-WROOM-02U Wi-Fi + BLE 5.0 module with u.FL connector",
    },
}


def extract_and_lock_user_components(prompt: str) -> list[dict[str, Any]]:
    """Extract user-specified parts from prompt and construct locked ComponentModel dicts."""
    user_parts = _extract_part_numbers(prompt)
    locked_components: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for part_name in user_parts:
        part_clean = part_name.strip()
        part_upper = part_clean.upper()

        known_info = None
        for k, info in _KNOWN_USER_PARTS.items():
            if k.upper() == part_upper or k.upper().replace("-", "") == part_upper.replace("-", ""):
                known_info = info
                break

        if known_info:
            id_str = known_info["id_str"]
            category = known_info["category"]
            description = known_info["description"]
            value = known_info.get("value", part_clean)
        else:
            exact = fuzzy_search_exact(part_clean)
            if exact and exact.get("id_str"):
                id_str = exact["id_str"]
                category = exact.get("category", id_str.split(":")[0] if ":" in id_str else "General")
                description = f"User-specified part {part_clean}"
                value = part_clean
            else:
                fallback = fuzzy_fallback(part_clean)
                if fallback and fallback.get("id_str"):
                    id_str = fallback["id_str"]
                    category = fallback.get("category", id_str.split(":")[0] if ":" in id_str else "General")
                    description = f"User-specified part {part_clean}"
                    value = part_clean
                else:
                    continue

        if id_str in seen_ids:
            continue
        seen_ids.add(id_str)

        func_id = make_functional_id(id_str, f"USER_LOCKED_{part_clean}")
        comp_model = ComponentModel(
            functional_id=func_id,
            id_str=id_str,
            category=category,
            description=description,
            value=value,
            is_user_locked=True,
            subsystem=f"User-specified ({part_clean})",
            justification=f"Exact user-requested part '{part_clean}' — hard locked against removal or substitution.",
        )
        locked_components.append(comp_model.to_dict())

    return locked_components
