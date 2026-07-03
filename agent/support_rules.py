"""Rule-based supporting component injection.

Each rule matches a selected component and returns a list of
supporting parts (caps, resistors, etc.) needed for it to function.
Rules are ordered most-specific-first; first match wins.
"""

import re
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
    # ── AVR MCUs WITH USB peripheral (ATmega*U4/U2, AT90USB*): RESET pull-up + AREF cap
    #     + VCC/AVCC decoupling + UCAP cap ──
    # Must come BEFORE generic AVR rule — first match wins.
    (
        lambda c: (
            bool(re.search(r'ATMEGA\w*US?B?\d', _id(c)))
            or any(kw in _id(c) for kw in ["ATMEGA32U4", "ATMEGA16U4", "ATMEGA32U2", "ATMEGA16U2", "ATMEGA8U2", "AT90USB"])
            or (_has_lib(c, "MCU_Microchip_AVR")
                and bool(re.search(r'ATMEGA\w*[US]\d', _id(c))))
        ),
        [
            {"search_query": "10k ohm resistor",  "preferred_id_str": "Device:R_Small",  "library_filter": "Device", "ref_des_prefix": "R", "description": "10kΩ ~RESET pull-up resistor (active-low reset stability)", "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF AREF decoupling cap",                              "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF VCC decoupling cap for AVR MCU",                    "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF AVCC decoupling cap for AVR MCU",                   "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF bulk decoupling cap for AVR MCU",                    "count": 1},
            {"search_query": "1uF capacitor",     "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "1µF UCAP decoupling cap for ATmega USB pad regulator",     "count": 1},
        ],
    ),
    # ── AVR MCUs WITHOUT USB (plain ATmega, ATtiny, AT90, ATxmega): RESET pull-up + AREF
    #     + VCC/AVCC decoupling — NO UCAP (these parts don't have a USB pad regulator) ──
    # Must come BEFORE the generic MCU rule — first match wins.
    (
        lambda c: (
            (any(kw in _id(c) for kw in ["ATMEGA", "ATTINY", "AT90", "ATXMEGA"])
             or _has_lib(c, "MCU_Microchip_AVR"))
            and not (
                bool(re.search(r'ATMEGA\w*US?B?\d', _id(c)))
                or any(kw in _id(c) for kw in ["ATMEGA32U4", "ATMEGA16U4", "ATMEGA32U2", "ATMEGA16U2", "ATMEGA8U2", "AT90USB"])
            )
        ),
        [
            {"search_query": "10k ohm resistor",  "preferred_id_str": "Device:R_Small",  "library_filter": "Device", "ref_des_prefix": "R", "description": "10kΩ ~RESET pull-up resistor (active-low reset stability)", "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF AREF decoupling cap",                              "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF VCC decoupling cap for AVR MCU",                    "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "100nF AVCC decoupling cap for AVR MCU",                   "count": 1},
            {"search_query": "small capacitor",   "preferred_id_str": "Device:C_Small",  "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF bulk decoupling cap for AVR MCU",                    "count": 1},
        ],
    ),
    # ── ESP32 / wireless MCU: USB-UART programming bridge ──
    # Must come BEFORE generic MCU rule — first match wins.
    # EXEMPT: DevKit/dev-board/module parts (WEMOS, NODEMCU, DEVKIT, MINI,
    # WROOM, RF_Module) — these already integrate the bridge, regulator,
    # and USB connector.
    (
        lambda c: (
            any(kw in (c.get("id_str", "") or "").upper()
                for kw in ["ESP32", "ESP8266"])
            and not any(kw in (c.get("id_str", "") or "").upper()
                        for kw in ["WEMOS", "NODEMCU", "DEVKIT", "MINI", "WROOM"])
            and not (c.get("id_str", "") or "").upper().startswith("RF_MODULE:")
        ),
        [
            {"search_query": "USB to UART bridge CP2102N", "preferred_id_str": "Interface_USB:CP2102N",
             "library_filter": "Interface_USB", "ref_des_prefix": "U",
             "description": "CP2102N USB-to-UART bridge for ESP32 programming", "count": 1},
            {"search_query": "small capacitor", "preferred_id_str": "Device:C_Small",
             "library_filter": "Device", "ref_des_prefix": "C",
             "description": "0.1µF decoupling cap for USB-UART bridge", "count": 2},
        ],
    ),
    # ── Microcontrollers / MCU modules: decoupling caps ──
    (
        lambda c: (
            (_has_lib(c, "MCU_", "Module_") or "MCU" in _id(c) or "ESP32" in _id(c) or "RP2040" in _id(c) or "STM32" in _id(c))
            and not any(dev in _id(c) for dev in ["WEMOS", "NODEMCU", "DEVKIT", "MINI", "WROOM"])
            and not _has_lib(c, "RF_Module")
            # Exclude AVR — already handled by the specific rule above
            and not any(kw in _id(c) for kw in ["ATMEGA", "ATTINY", "AT90", "ATXMEGA"])
            and not _has_lib(c, "MCU_Microchip_AVR")
        ),
        [
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "0.1µF decoupling cap for MCU", "count": 2},
            {"search_query": "small capacitor",  "preferred_id_str": "Device:C_Small", "library_filter": "Device", "ref_des_prefix": "C", "description": "10µF bulk decoupling cap for MCU", "count": 1},
        ],
    ),
    # ── 1-Wire sensors: data line pull-up (must match before generic Sensor rule) ──
    (
        lambda c: any(kw in _id(c) for kw in ["DS18B20", "DS18S20", "DS1822", "DS18", "1-WIRE"]),
        [
            {"search_query": "4.7k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "4.7kΩ 1-Wire data line pull-up", "count": 1},
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
    # ── USB connector (USB-C): CC resistors + ESD protection ──
    (
        lambda c: _has_lib(c, "Connector") and "USB" in _id(c) and ("TYPE-C" in _id(c) or "USB_C" in _id(c)),
        [
            {"search_query": "5.1k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "5.1kΩ USB-C CC pull-down", "count": 2},
            {"search_query": "USB ESD protection", "preferred_id_str": "Connector_USB:TPD6S300A", "library_filter": "Connector_USB", "ref_des_prefix": "U", "description": "TPD6S300A USB-C ESD protection", "count": 1},
        ],
    ),
    # ── I²C devices: pull-up resistors ──
    (
        lambda c: (
            "I2C" in _id(c) or "SDA" in _id(c) or "SCL" in _id(c)
            or _has_lib(c, "Sensor")
            or any(kw in ((c.get("description", "") or "") + (c.get("text", "") or "")).upper()
                   for kw in ["I2C", "SDA ", "SCL ", "TWI", "I²C"])
        ),
        [
            {"search_query": "4.7k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "4.7kΩ I²C SDA pull-up resistor", "count": 1},
            {"search_query": "4.7k ohm resistor", "preferred_id_str": "Device:R_Small", "library_filter": "Device", "ref_des_prefix": "R", "description": "4.7kΩ I²C SCL pull-up resistor", "count": 1},
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


# ── Semantic category builder functions ────────────────────────────────────
# These set category to the semantic type (CAPACITOR, RESISTOR, POLYFUSE)
# instead of the KiCad library prefix ("Device"), which fixes column
# assignment in _get_column_for_category().

def _make_cap(ref_des: str, value: str, for_ref: str) -> dict:
    return {
        "ref_des":     ref_des,
        "id_str":      "Device:C_Small",
        "category":    "CAPACITOR",
        "description": f"{value} decoupling cap for {for_ref}",
        "footprint":   "",
        "pads":        [],
    }

def _make_resistor(ref_des: str, value: str, for_ref: str) -> dict:
    return {
        "ref_des":     ref_des,
        "id_str":      "Device:R_Small",
        "category":    "RESISTOR",
        "description": f"{value} resistor for {for_ref}",
        "footprint":   "",
        "pads":        [],
    }

def _make_polyfuse(ref_des: str, value: str, for_ref: str) -> dict:
    return {
        "ref_des":     ref_des,
        "id_str":      "Device:Polyfuse",
        "category":    "POLYFUSE",
        "description": f"{value} polyfuse for {for_ref}",
        "footprint":   "",
        "pads":        [],
    }


# ── Known fallback symbol map ──────────────────────────────────────────────
# Used when the preferred_id_str is not found in RAG results, so protection
# ICs don't end up in the wrong library (e.g. Connector_USB:TPD6S300A).

KNOWN_FALLBACK_SYMBOLS: dict[str, str] = {
    "TPD6S300A":    "Device:TPD6S300A",
    "USBLC6-2SC6":  "Device:USBLC6-2SC6",
    "IP4234CZ10":   "Device:IP4234CZ10",
    "SRV05-4":      "Device:SRV05-4",
    "Crystal":      "Device:Crystal",
    "Crystal_GND24":"Device:Crystal_GND24",
}

def resolve_fallback_symbol(part_name: str) -> str | None:
    for key, symbol in KNOWN_FALLBACK_SYMBOLS.items():
        if key.upper() in part_name.upper():
            return symbol
    return None
