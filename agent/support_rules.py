"""Rule-based supporting component injection.

Each rule matches a selected component and returns a list of
supporting parts (caps, resistors, etc.) needed for it to function.
Rules are ordered most-specific-first; first match wins.
"""

from typing import Callable

SupportDef = dict
"""Shape:
{
    "search_query": str,        # what to search for (e.g. "0.1uF capacitor")
    "preferred_id_str": str,    # exact id_str to prefer if it exists
    "library_filter": str,      # restrict search to this library (e.g. "Device")
    "ref_des_prefix": str,      # ref designator prefix (C, R, L, etc.)
    "description": str,         # human-readable purpose
    "count": int,               # how many instances needed
}
"""

Rule = tuple[Callable[[dict], bool], list[SupportDef]]


def _lib(c: dict) -> str:
    return (c.get("id_str", "") or "").partition(":")[0].upper()


def _id(c: dict) -> str:
    return (c.get("id_str", "") or "").upper()


def _cat(c: dict) -> str:
    return ((c.get("category", "") or "") + " " + _lib(c)).upper()


def _has_lib(c: dict, *libs: str) -> bool:
    return any(lib.upper() in _lib(c) for lib in libs)


RULES: list[Rule] = [
    # ── Voltage regulators: input & output caps ──
    (
        lambda c: _has_lib(c, "Regulator_Linear", "Regulator_Switching"),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF input bulk cap for regulator",  "count": 1},
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF input bypass cap for regulator", "count": 1},
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF output bulk cap for regulator", "count": 1},
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF output bypass cap for regulator", "count": 1},
        ],
    ),
    # ── Microcontrollers / MCU modules: decoupling caps ──
    (
        lambda c: _has_lib(c, "MCU_", "Module_") or "MCU" in _id(c) or "ESP32" in _id(c) or "RP2040" in _id(c) or "STM32" in _id(c),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF decoupling cap for MCU", "count": 2},
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF bulk decoupling cap for MCU", "count": 1},
        ],
    ),
    # ── Sensors: decoupling cap ──
    (
        lambda c: _has_lib(c, "Sensor"),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF decoupling cap for sensor", "count": 1},
        ],
    ),
    # ── Interface ICs (USB, CAN, Ethernet, etc.): decoupling cap ──
    (
        lambda c: _has_lib(c, "Interface"),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF decoupling cap for interface IC", "count": 1},
        ],
    ),
    # ── Op-amps / Comparators: decoupling cap ──
    (
        lambda c: _has_lib(c, "Amplifier", "Comparator"),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF decoupling cap for op-amp", "count": 1},
        ],
    ),
    # ── LED: current-limiting resistor ──
    (
        lambda c: _id(c).startswith("DEVICE:LED") or _id(c).startswith("DEVICE:D_"),
        [
            {"search_query": "330 ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "330Ω current limit for LED", "count": 1},
        ],
    ),
    # ── Crystal: load capacitors ──
    (
        lambda c: _id(c).startswith("DEVICE:CRYSTAL") or _has_lib(c, "Crystal"),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "18pF crystal load cap", "count": 2},
        ],
    ),
    # ── USB connector (USB-C): CC resistors ──
    (
        lambda c: _has_lib(c, "Connector") and "USB" in _id(c) and ("TYPE-C" in _id(c) or "USB_C" in _id(c)),
        [
            {"search_query": "5.1k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "5.1kΩ USB-C CC pull-down", "count": 2},
        ],
    ),
    # ── General connector (audio jacks, headers, etc.) ──
    (
        lambda c: _has_lib(c, "Connector"),
        [],  # No auto-injection for generic connectors
    ),
    # ── Transistors / FETs: base/gate resistor ──
    (
        lambda c: _has_lib(c, "Transistor", "FET"),
        [
            {"search_query": "10k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "10kΩ base/gate resistor", "count": 1},
        ],
    ),
]


def _is_itself_supporting(c: dict) -> bool:
    """Skip injection for components that ARE the supporting parts (C, R, L, etc)."""
    id_str = (c.get("id_str", "") or "").upper()
    if not id_str.startswith("DEVICE:"):
        return False
    name = id_str.partition(":")[2]
    if name in ("C", "R", "L") or name.startswith(("C_", "R_", "L_", "CP")):
        return True
    return False


def get_supporting_components(component: dict) -> list[SupportDef]:
    """Return list of supporting component definitions for *component*.

    Returns empty list if no rules match or component is itself a supporting part.
    """
    if _is_itself_supporting(component):
        return []
    for predicate, parts in RULES:
        if predicate(component):
            return parts
    return []
