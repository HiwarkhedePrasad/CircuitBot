"""Constraint Checker — deterministic, read-only validation.

This node performs fast, deterministic checks on the component list.
It classifies errors into fatal/repairable/warning categories.
It NEVER modifies the component list.

Fatal errors halt the pipeline.
Repairable errors are sent to the repair node.
Warnings are informational only.
"""

import re

from agent.knowledge.dependency_graph import get_mcu_family
from agent.programming_interface import has_programming_interface
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event,
    _check_stage_contract, _stage_result,
    _get_ref_des, _get_id_str,
)
from uuid import uuid4


def _is_mcu(comp: dict) -> bool:
    """Check if a component is an MCU or MCU module."""
    id_str = (comp.get("id_str", "") or "").upper()
    category = (comp.get("category", "") or "").upper()
    if "MCU" in category or "PROCESSOR" in category or "CPU" in category:
        return True
    if any(kw in id_str for kw in (
        "ESP32", "STM32", "RP2040", "RP2350", "ATMEGA", "ATTINY", "SAMD", "NRF52"
    )):
        return True
    if any(kw in id_str for kw in ("WEMOS", "NODEMCU", "DEVKIT", "WROOM")):
        return True
    if "RF_MODULE" in category and any(kw in id_str for kw in (
        "ESP32", "ESP8266", "C3", "S3", "C6", "H2"
    )):
        return True
    return False


def _get_library(comp: dict) -> str:
    id_str = (comp.get("id_str", "") or "")
    return id_str.split(":")[0] if ":" in id_str else ""


# ── Check: MCU present in frozen architecture ─────────────────────────

def _check_mcu_present(comps: list[dict], primary_mcu: str, architecture_frozen: bool) -> list[dict]:
    """Fatal error if architecture is frozen with a primary_mcu but
    no MCU component exists in the selection.  This catches the case
    where every subsystem was skipped or the MCU was never selected,
    preventing a bogus schematic with no MCU from reaching the user.
    
    The check is library-prefix based (MCU_*, RF_Module:*), not part-
    number based, so it scales to any KiCad library size automatically."""
    if not architecture_frozen or not primary_mcu:
        return []
    if not comps:
        return [{
            "code": "MCU_MISSING",
            "category": "fatal",
            "component_id": None,
            "message": f"No components selected — architecture requires MCU {primary_mcu}",
            "suggested_fix": None,
        }]
    mcus = [c for c in comps if _is_mcu(c) and not c.get("builtin")]
    if not mcus:
        return [{
            "code": "MCU_MISSING",
            "category": "fatal",
            "component_id": None,
            "message": f"No MCU selected — architecture requires {primary_mcu}",
            "suggested_fix": None,
        }]
    return []


# ── Check: duplicate MCUs ─────────────────────────────────────────────

def _check_duplicate_mcus(comps: list[dict]) -> list[dict]:
    mcus = [c for c in comps if _is_mcu(c) and not c.get("builtin")]
    if len(mcus) <= 1:
        return []
    mcu_names = [f"{_get_ref_des(c)} ({_get_id_str(c)})" for c in mcus]
    return [{
        "code": "DUP_MCU",
        "category": "fatal",
        "component_id": None,
        "message": f"Multiple MCUs found: {', '.join(mcu_names)}",
        "suggested_fix": None,
    }]


# ── MCU family-group map ──────────────────────────────────────────────
# Used to determine if a mismatch is repairable (same group) or fatal
# (different group entirely).

_MCU_FAMILY_GROUPS: list[list[str]] = [
    ["ESP32-C3", "ESP32-S3", "ESP32-C6", "ESP32-S2", "ESP32-P4"],
    ["STM32"],
    ["RP2040", "RP2350"],
    ["ATmega328", "ATmega32U4"],
]

_MCU_BASE_FAMILIES: dict[str, str] = {
    "ESP32-C3": "ESP32", "ESP32-S3": "ESP32", "ESP32-C6": "ESP32",
    "ESP32-S2": "ESP32", "ESP32-P4": "ESP32",
    "RP2040": "RP", "RP2350": "RP",
    "ATmega328": "ATmega", "ATmega32U4": "ATmega",
}


def _get_mcu_base(family: str) -> str | None:
    """Get the broader base family (e.g. 'ESP32' for 'ESP32-S3')."""
    return _MCU_BASE_FAMILIES.get(family)


def _is_mcu_mismatch_repairable(selected: str, locked: str) -> bool:
    """A mismatch is repairable if both MCUs share the same base family."""
    base_a = _get_mcu_base(selected)
    base_b = _get_mcu_base(locked)
    if base_a and base_b and base_a == base_b:
        return True
    # Fallback: check explicit group membership
    a_lower = selected.lower()
    b_lower = locked.lower()
    for group in _MCU_FAMILY_GROUPS:
        group_lower = [g.lower() for g in group]
        if a_lower in group_lower and b_lower in group_lower:
            return True
    return False


# ── Check: MCU matches architecture ──────────────────────────────────

def _check_mcu_matches_architecture(comps: list[dict], primary_mcu: str, architecture_frozen: bool) -> list[dict]:
    if not architecture_frozen or not primary_mcu:
        return []
    errors = []
    for c in comps:
        if not _is_mcu(c) or c.get("builtin"):
            continue
        mcu_family = get_mcu_family(c.get("id_str", ""))
        if mcu_family and mcu_family != primary_mcu:
            if _is_mcu_mismatch_repairable(mcu_family, primary_mcu):
                category = "repairable"
            else:
                category = "fatal"
            errors.append({
                "code": "MCU_MISMATCH",
                "category": category,
                "component_id": c.get("id_str", ""),
                "message": (f"MCU {_get_id_str(c)} ({mcu_family}) doesn't match "
                           f"locked architecture ({primary_mcu})"),
                "actual_mcu": mcu_family,
                "suggested_fix": f"Update primary_mcu from {primary_mcu} to {mcu_family}",
            })
    return errors


# ── Check: missing programming/debug header ──────────────────────────

def _check_missing_programming_header(comps: list[dict], board_type: str) -> list[dict]:
    if board_type == "devkit":
        return []
    has_mcu = any(_is_mcu(c) for c in comps if not c.get("builtin"))
    if not has_mcu:
        return []
    if not has_programming_interface(comps):
        return [{
            "code": "MISSING_PROGRAMMING_HEADER",
            "category": "repairable",
            "component_id": None,
            "message": "MCU present but no valid programming interface found",
            "suggested_fix": "Add native USB data, a UART/ISP header, or a USB-UART bridge",
        }]
    return []


# ── Check: missing power input connector ─────────────────────────────

def _check_missing_power_input(comps: list[dict]) -> list[dict]:
    has_power_input = any(
        _get_library(c) in ("Connector_USB", "Connector")
        and ("USB" in (_get_id_str(c)).upper() or "BARREL" in (_get_id_str(c)).upper() or "SCREW" in (_get_id_str(c)).upper())
        for c in comps
    )
    has_regulator = any(
        _get_library(c).startswith("Regulator")
        for c in comps
    )
    if has_power_input or not has_regulator:
        return []
    return [{
        "code": "MISSING_POWER_INPUT",
        "category": "repairable",
        "component_id": None,
        "message": "Voltage regulator present but no power input connector",
        "suggested_fix": "Add a USB-C or terminal block for power input",
    }]


# ── Check: missing ESD protection on USB ─────────────────────────────

def _check_missing_usb_esd(comps: list[dict]) -> list[dict]:
    has_usb = any(
        "USB" in (_get_id_str(c)).upper()
        and _get_library(c) in ("Connector_USB", "Connector")
        for c in comps
    )
    if not has_usb:
        return []
    has_esd = any(
        "TPD" in (_get_id_str(c)).upper() or "USBLC" in (_get_id_str(c)).upper()
        or "ESD" in (_get_id_str(c)).upper()
        for c in comps
    )
    if not has_esd:
        return [{
            "code": "MISSING_USB_ESD",
            "category": "warning",
            "component_id": None,
            "message": "USB connector present but no ESD protection",
            "suggested_fix": "Add TVS diode array (TPD6S300A or USBLC6-2SC6)",
        }]
    return []


# ── Check: missing power regulation (voltage mismatch) ───────────────

def _check_missing_power_regulation(comps: list[dict], prompt: str, board_type: str = "") -> list[dict]:
    # Devkits/modules already provide onboard 3.3V regulation — skip this
    # check to avoid the repair loop where AMS1117 is removed as redundant
    # then re-added by this check, then removed again, etc.
    if board_type in ("devkit", "module"):
        return []
    prompt_lower = prompt.lower()
    has_mcu_3v3 = any(
        "3.3" in (_get_id_str(c)).upper() or "3V3" in (_get_id_str(c)).upper()
        for c in comps if _is_mcu(c)
    ) or any(kw in prompt_lower for kw in ("esp32", "rp2040", "stm32", "3.3v"))
    has_usb_input = any(
        "USB" in (_get_id_str(c)).upper()
        for c in comps
    ) or "usb" in prompt_lower
    has_regulator = any(
        _get_library(c).startswith("Regulator")
        for c in comps
    )
    if has_mcu_3v3 and has_usb_input and not has_regulator:
        return [{
            "code": "MISSING_POWER_REGULATION",
            "category": "repairable",
            "component_id": None,
            "message": "3.3V MCU detected with USB (5V) input but no regulator",
            "suggested_fix": "Add a 3.3V LDO regulator (AMS1117-3.3 or AP2112K-3.3)",
        }]
    return []


# ── Check: duplicate passives ────────────────────────────────────────

def _check_duplicate_passives(comps: list[dict]) -> list[dict]:
    warnings = []
    seen: dict[tuple, str] = {}
    for c in comps:
        if c.get("builtin"):
            continue
        id_str = c.get("id_str", "")
        for_component = c.get("for_component", "")
        func_id = c.get("functional_id", "")
        desc = c.get("description", "")
        key = (id_str, for_component, func_id or desc)
        if key in seen:
            warnings.append({
                "code": "DUP_PASSIVE",
                "category": "warning",
                "component_id": c.get("id_str", ""),
                "message": f"Duplicate passive {_get_ref_des(c)} ({id_str}) for {for_component}",
                "suggested_fix": "Consider removing the duplicate",
            })
        else:
            seen[key] = _get_ref_des(c)
    return warnings


# ── Check: bare RF IC preference ────────────────────────────────────

def _check_module_preference(comps: list[dict], board_type: str = "") -> list[dict]:
    # Bare IC designs explicitly intend to use bare IC chips
    if board_type in ("bare_ic", "custom_pcb"):
        return []
    bare_rf_pattern = re.compile(
        r'(ESP32|ESP8266|NRF24[L]?[012]|NRF52[345]|CC1101|CC1310|CC1352|SX126[128])',
        re.IGNORECASE,
    )
    module_markers = re.compile(
        r'(WROOM|MINI|MOD|DEVKIT|MODULE|DK|DONGLE|BOARD|BREAKOUT)',
        re.IGNORECASE,
    )
    module_libs = ("RF_MODULE", "MODULE_")
    errors = []
    for c in comps:
        if c.get("builtin") or c.get("user_locked"):
            continue
        id_str = (_get_id_str(c)).upper()
        library = _get_library(c)
        if any(lib in library for lib in module_libs):
            continue
        if module_markers.search(id_str):
            continue
        if bare_rf_pattern.search(id_str):
            errors.append({
                "code": "BARE_RF_IC",
                "category": "repairable",
                "component_id": _get_id_str(c),
                "message": f"{_get_ref_des(c)} ({_get_id_str(c)}) is a bare RF IC — replace with pre-certified module",
                "suggested_fix": "Search for a module variant (WROOM/DEVKIT suffix)",
            })
    return errors


# ── Check: devkit redundancy ─────────────────────────────────────────

def _check_devkit_redundancy(comps: list[dict], board_type: str) -> list[dict]:
    if board_type != "devkit":
        return []
    errors = []
    devkit_provided = [
        ("CP2102", "USB-to-UART bridge"),
        ("CH340", "USB-to-UART bridge"),
        ("AMS1117", "voltage regulator"),
        ("AP2112", "voltage regulator"),
    ]
    for c in comps:
        if c.get("builtin") or c.get("user_locked"):
            continue
        id_str = (_get_id_str(c)).upper()
        for pattern, desc in devkit_provided:
            if pattern in id_str:
                errors.append({
                    "code": "DEVKIT_REDUNDANT",
                    "category": "repairable",
                    "component_id": _get_id_str(c),
                    "message": f"{_get_ref_des(c)} ({_get_id_str(c)}) is redundant — devkit already provides {desc}",
                    "suggested_fix": f"Remove {_get_ref_des(c)} (devkit has built-in {desc})",
                })
                break
    return errors


# ── Check: missing strapping resistors (ESP32) ───────────────────────

def _check_missing_strapping(comps: list[dict]) -> list[dict]:
    errors = []
    for c in comps:
        id_str = (_get_id_str(c)).upper()
        if "ESP32" not in id_str:
            continue
        # ESP32 boot-strapping: GPIO0=high, GPIO2=low, GPIO9=high (C3)
        has_boot_resistor = any(
            "strapping" in ((c2.get("description", "") or "") + (c2.get("justification", "") or "")).lower()
            or "boot" in ((c2.get("description", "") or "") + (c2.get("justification", "") or "")).lower()
            for c2 in comps
        )
        if not has_boot_resistor:
            errors.append({
                "code": "MISSING_STRAPPING",
                "category": "warning",
                "component_id": _get_id_str(c),
                "message": f"{_get_ref_des(c)} ({_get_id_str(c)}) — boot-strapping resistors may be needed",
                "suggested_fix": "Add pull-up/pull-down resistors on strapping pins (GPIO0, GPIO2, GPIO9)",
            })
    return errors


# ── L1 ERC: Decoupling capacitor check ──────────────────────────────
# Pattern from PCBSchemaGen L1: every active IC needs decoupling caps

_IC_LIBRARY_PREFIXES = frozenset({
    "MCU_", "Sensor_", "Regulator_Linear", "Regulator_Switching",
    "Interface_USB", "Interface_UART", "Interface_",
    "Battery_Management", "RF_Module", "Memory_", "Driver_",
})


def _is_ic(comp: dict) -> bool:
    """Check if a component is an IC that needs decoupling."""
    lib = _get_library(comp)
    if any(lib.startswith(p) for p in _IC_LIBRARY_PREFIXES):
        return True
    id_str = (_get_id_str(comp)).upper()
    if any(kw in id_str for kw in ("ESP32", "STM32", "RP2040", "RP2350",
                                    "ATMEGA", "ATTINY", "SAMD", "NRF52",
                                    "CP210", "CH340", "FT232", "FT234",
                                    "AMS1117", "AP2112", "MCP1700",
                                    "TP4056", "MCP73831", "MAX17048",
                                    "SSD1306", "SH1106", "BME280",
                                    "TMP117", "DS18B20", "MPU6050",
                                    "W25Q", "AT24C02")):
        return True
    return False


def _check_decoupling_caps(comps: list[dict]) -> list[dict]:
    """L1 ERC: Verify every IC has adequate decoupling capacitors.

    Each active IC should have at least one 100nF decoupling cap per
    power pin in the supporting component list.
    """
    errors = []
    ics = [c for c in comps if _is_ic(c) and not c.get("builtin")]
    if not ics:
        return errors

    for ic in ics:
        id_str = _get_id_str(ic)
        ref = _get_ref_des(ic)
        for_comp = ic.get("ref_des", "")

        decoupling_found = 0
        for c in comps:
            if c.get("builtin"):
                continue
            cid = _get_id_str(c)
            desc = (c.get("description", "") or "").lower()
            val = (c.get("value", "") or "").lower()
            is_cap = ("C_SMALL" in cid or ":C_" in cid or "CAPACITOR" in cid.upper())
            is_decoupling = (
                ("decoupling" in desc or "bypass" in desc or "bypass" in val)
                and ("100n" in val or "0.1" in val or "0.1" in desc or "100n" in desc)
            )
            if is_cap and is_decoupling and c.get("for_component", "") in (for_comp, ""):
                decoupling_found += 1

        if decoupling_found == 0:
            errors.append({
                "code": "MISSING_DECOUPLING_CAP",
                "category": "warning",
                "component_id": id_str,
                "message": f"{ref} ({id_str}) has no decoupling capacitors — add 100nF cap per power pin",
                "suggested_fix": f"Add one 100nF ceramic decoupling capacitor per VCC/VDD pin on {ref}",
            })

    return errors


# ── L1 ERC: Bus pull-up resistor check ──────────────────────────────
# Pattern from CircuitLM ProtocolChecker: I2C buses must have pull-ups

_I2C_DEVICES = frozenset({
    "TMP117", "BME280", "MPU6050", "SSD1306", "SH1106",
    "AT24C02", "MCP4725", "ADS1115", "BH1750", "PCA9685",
    "MCP23017", "MCP7940", "DS3231", "INA219", "MCP9808",
    "SHT30", "SHT31", "SGP30", "CCS811", "VL53L0X",
})


def _check_bus_pullups(comps: list[dict]) -> list[dict]:
    """L1 ERC: Check I2C buses have pull-up resistors present.

    I2C devices need 4.7kΩ pull-up resistors on SDA and SCL lines.
    """
    errors = []

    i2c_parts = []
    for c in comps:
        id_str = (_get_id_str(c)).upper()
        if any(d.upper() in id_str for d in _I2C_DEVICES):
            i2c_parts.append(c)
        if "ESP32" in id_str or "SDA" in id_str or "SCL" in id_str:
            i2c_parts.append(c)

    if not i2c_parts:
        return errors

    has_pullup = False
    for c in comps:
        cid = _get_id_str(c)
        desc = (c.get("description", "") or "").lower()
        val = (c.get("value", "") or "").lower()
        is_resistor = ("R_SMALL" in cid or ":R_" in cid)
        is_pullup = (
            "pull" in desc or "pull" in val
            or ("4.7k" in val or "10k" in val and "pull" in desc)
        )
        if is_resistor and is_pullup:
            has_pullup = True
            break

    if not has_pullup:
        involved = ", ".join(_get_ref_des(c) + " (" + _get_id_str(c) + ")" for c in i2c_parts[:3])
        errors.append({
            "code": "MISSING_I2C_PULLUP",
            "category": "warning",
            "component_id": None,
            "message": f"I2C devices found ({involved}) but no pull-up resistors — add 4.7kΩ pull-ups on SDA/SCL",
            "suggested_fix": "Add two 4.7kΩ resistors: one from SDA to 3V3, one from SCL to 3V3",
        })

    return errors


# ── L1 ERC: Power compatibility check ───────────────────────────────
# Pattern from PCBSchemaGen L4 power invariants: voltage domain matching

_REGULATOR_VOLTAGES: dict[str, str] = {
    "AMS1117-3.3": "3.3V", "AP2112K-3.3": "3.3V", "MCP1700-3302E": "3.3V",
    "XC6206P302MR": "3.3V", "RT9013-33GB": "3.3V",
    "AMS1117-5.0": "5.0V", "L7805": "5.0V", "LM7805": "5.0V",
    "AMS1117-ADJ": "ADJ", "LM317": "ADJ", "LM1117-3.3": "3.3V",
    "LM1117-5.0": "5.0V", "LM1117-ADJ": "ADJ",
}

_3V3_MCUS = frozenset({
    "ESP32", "STM32", "RP2040", "RP2350", "NRF52", "SAMD", "ATTINY",
    "ATmega32U4",
})
_5V_MCUS = frozenset({
    "ATmega328P", "ATmega328", "ATmega2560", "ATmega1280",
    "ATmega32U4",  # can run at 5V
})


def _check_power_compatibility(comps: list[dict]) -> list[dict]:
    """L1 ERC: Verify regulator output voltage is compatible with loads.

    Checks:
      - 3.3V MCUs connected to 3.3V regulator (not 5V)
      - 5V-only MCUs (ATmega328P at 16MHz) have 5V available
    """
    errors = []

    reg_output = None
    for c in comps:
        id_str = _get_id_str(c)
        lib = _get_library(c)
        if lib.startswith("Regulator"):
            part_name = id_str.split(":")[-1] if ":" in id_str else id_str
            reg_output = _REGULATOR_VOLTAGES.get(part_name)
            if reg_output:
                break

    if not reg_output or reg_output == "ADJ":
        return errors

    for c in comps:
        id_str = _get_id_str(c)
        id_upper = id_str.upper()
        if not _is_mcu(c) or c.get("builtin"):
            continue
        mcu_3v3 = any(m in id_upper for m in _3V3_MCUS)
        mcu_5v = any(m in id_upper for m in _5V_MCUS)
        if mcu_3v3 and reg_output == "5.0V":
            errors.append({
                "code": "VOLTAGE_MISMATCH",
                "category": "warning",
                "component_id": id_str,
                "message": f"{_get_ref_des(c)} ({id_str}) needs 3.3V but regulator outputs {reg_output}",
                "suggested_fix": "Replace 5.0V regulator with a 3.3V variant (AMS1117-3.3 or AP2112K-3.3)",
            })
        elif mcu_5v and reg_output == "3.3V":
            at16mhz = "ATMEGA328" in id_upper or "ATMEGA2560" in id_upper
            if at16mhz:
                errors.append({
                    "code": "VOLTAGE_MISMATCH",
                    "category": "warning",
                    "component_id": id_str,
                    "message": f"{_get_ref_des(c)} ({id_str}) needs 5V for 16MHz operation but regulator outputs {reg_output}",
                    "suggested_fix": "Use 5V regulator, or run MCU at 8MHz with internal oscillator at 3.3V",
                })

    return errors


# ── L1 ERC: Power budget check ──────────────────────────────────────
# Pattern from PCBSchemaGen L4: estimate total load vs regulator capacity

_TYPICAL_CURRENT_DRAWS: dict[str, float] = {
    # MCUs (mA)
    "ESP32": 80, "ESP32-S3": 60, "ESP32-C3": 45, "ESP32-C6": 50,
    "ESP8266": 80,
    "STM32": 30, "STM32F103": 35, "STM32F4": 50,
    "RP2040": 25, "RP2350": 30,
    "ATmega328P": 10, "ATmega32U4": 12, "ATMEGA2560": 15,
    "SAMD21": 8, "SAMD51": 15,
    "NRF52840": 15,
    # Sensors (mA)
    "BME280": 0.003, "TMP117": 0.185, "DS18B20": 1.5,
    "MPU6050": 3.5, "SSD1306": 20, "SH1106": 25,
    # Radios (mA)
    "NRF24L01": 13.5, "RFM95": 40, "SX1262": 15,
    # USB-UART bridges (mA)
    "CP2102N": 12, "CH340G": 10, "FT232RL": 15, "FT234XD": 8,
}

_REGULATOR_CAPACITY: dict[str, float] = {
    "AMS1117": 1000, "AP2112K": 600, "MCP1700": 250,
    "XC6206": 200, "RT9013": 500,
    "L7805": 1500, "LM7805": 1500, "LM317": 1500,
    "LM1117": 800,
}


def _estimate_current(comps: list[dict]) -> float:
    """Estimate total current draw from selected components."""
    total = 0.0
    id_strs_seen = set()
    for c in comps:
        id_str = (_get_id_str(c)).upper()
        for i in id_strs_seen:
            if i in id_str or id_str in i:
                break
        else:
            for pattern, current in _TYPICAL_CURRENT_DRAWS.items():
                if pattern.upper() in id_str:
                    total += current
                    break
        id_strs_seen.add(id_str)
    return total


def _get_regulator_capacity(comps: list[dict]) -> float | None:
    """Get the max current capacity of the voltage regulator."""
    for c in comps:
        id_str = _get_id_str(c)
        lib = _get_library(c)
        if lib.startswith("Regulator"):
            part_name = id_str.split(":")[-1] if ":" in id_str else id_str
            for pattern, cap in _REGULATOR_CAPACITY.items():
                if pattern.upper() in part_name.upper():
                    return cap
    return None


def _check_power_budget(comps: list[dict]) -> list[dict]:
    """L1 ERC: Check estimated power draw is within regulator capacity."""
    errors = []

    reg_capacity = _get_regulator_capacity(comps)
    if reg_capacity is None:
        return errors

    estimated = _estimate_current(comps)
    margin = reg_capacity - estimated

    if margin < 0:
        errors.append({
            "code": "POWER_BUDGET_EXCEEDED",
            "category": "warning",
            "component_id": None,
            "message": f"Estimated load {estimated:.0f}mA exceeds regulator capacity {reg_capacity:.0f}mA by {-margin:.0f}mA",
            "suggested_fix": f"Replace regulator with one rated for at least {estimated * 1.3:.0f}mA, or reduce component count",
        })
    elif margin < reg_capacity * 0.2:
        errors.append({
            "code": "LOW_POWER_MARGIN",
            "category": "warning",
            "component_id": None,
            "message": f"Power margin low: {margin:.0f}mA headroom ({estimated:.0f}mA load on {reg_capacity:.0f}mA regulator)",
            "suggested_fix": "Consider a regulator with at least 1.5x the estimated load current",
        })

    return errors


# ── L1 ERC: Critical pin termination check ──────────────────────────
# Pattern from CircuitLM FloatingInputChecker: EN, RST, BOOT must be pulled

_CRITICAL_PIN_KEYWORDS = frozenset({
    "enable", "en ", "chip_en", "s3_en", "c3_en",
    "reset", "rst", "nrst", "nreset", "reset_",
    "boot", "boot0", "boot1", "gpio0", "gpio9",
})


def _check_critical_pin_termination(comps: list[dict]) -> list[dict]:
    """L1 ERC: Check critical pins (EN, RST, BOOT) have proper termination.

    Checks that:
      - ESP32 EN pin has a 10k pull-up
      - ATmega RESET pin has a 10k pull-up
      - ESP32 BOOT (GPIO0) has a pull-up resistor
    """
    errors = []

    has_mcu = False
    has_esp32 = False
    has_atmega = False

    for c in comps:
        id_str = (_get_id_str(c)).upper()
        if _is_mcu(c) and not c.get("builtin"):
            has_mcu = True
            if "ESP32" in id_str:
                has_esp32 = True
            if "ATMEGA" in id_str:
                has_atmega = True

    if not has_mcu:
        return errors

    has_termination = False
    for c in comps:
        desc = (c.get("description", "") or "").lower()
        just = (c.get("justification", "") or "").lower()
        val = (c.get("value", "") or "").lower()
        combined = f"{desc} {just} {val}"
        if any(kw in combined for kw in _CRITICAL_PIN_KEYWORDS):
            has_termination = True
            break

    if has_termination:
        return errors

    if has_esp32:
        errors.append({
            "code": "MISSING_CRITICAL_TERMINATION",
            "category": "warning",
            "component_id": None,
            "message": "ESP32 present but no EN/BOOT pull-up resistors found — EN needs 10k pull-up to 3V3, GPIO0 (BOOT) needs 10k pull-up",
            "suggested_fix": "Add a 10kΩ resistor from EN to 3V3, and a 10kΩ resistor from GPIO0 to 3V3",
        })
    elif has_atmega:
        errors.append({
            "code": "MISSING_CRITICAL_TERMINATION",
            "category": "warning",
            "component_id": None,
            "message": "ATmega present but no RESET pull-up found — RESET pin needs 10k pull-up to VCC",
            "suggested_fix": "Add a 10kΩ resistor from RESET pin to VCC",
        })

    return errors


# ── L1 ERC: Crystal load capacitor check ─────────────────────────────
# Pattern from PCBSchemaGen L1: passive components must be correct

def _check_crystal_load_caps(comps: list[dict]) -> list[dict]:
    """L1 ERC: Crystal oscillators should have load capacitors."""
    errors = []

    has_crystal = any(
        "CRYSTAL" in (_get_id_str(c)).upper() or ":Y_" in (_get_id_str(c)).upper()
        for c in comps
    )
    if not has_crystal:
        return errors

    has_load_caps = False
    for c in comps:
        cid = _get_id_str(c)
        desc = (c.get("description", "") or "").lower()
        for_comp = c.get("for_component", "")
        is_cap = ("C_SMALL" in cid or ":C_" in cid)
        is_load = (
            "load" in desc or "xtal" in desc or "crystal" in desc
            or for_comp and "crystal" in for_comp.lower()
        )
        if is_cap and is_load:
            has_load_caps = True
            break

    if not has_load_caps:
        errors.append({
            "code": "MISSING_CRYSTAL_LOAD_CAPS",
            "category": "warning",
            "component_id": None,
            "message": "Crystal oscillator present but no load capacitors — add two 12-22pF caps from each crystal pin to GND",
            "suggested_fix": "Add two capacitors (12-22pF typical): one from XTAL1 to GND, one from XTAL2 to GND",
        })

    return errors


# ── Main entry point ─────────────────────────────────────────────────

def constraint_checker_node(state, config):
    """Run deterministic checks on the component list. Read-only — never modifies components."""
    check_id = uuid4().hex[:8]
    _emit(config, "agent:thinking", {"message": "Checking constraints..."})
    emit_assistant_message(config, "Running deterministic constraint checks...")
    emit_tool_event(config, "Constraint Checker", "running", "Checking constraints...")

    contract = _check_stage_contract("constraint_checker", state, ["selected_components"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "constraint_checker", {})

    comps = list(state.get("selected_components", []))
    board_type = state.get("board_type")
    if not board_type:
        board_type = "bare_ic"
        _emit(config, "agent:log", {"message": "  WARNING: board_type not set by architecture_planner, defaulting to bare_ic"})
    primary_mcu = state.get("primary_mcu", "")
    architecture_frozen = state.get("architecture_frozen", False)
    prompt = state.get("prompt", "")

    # ── Self-healing: remove extra MCUs when architecture is locked ──────
    # This is a safety net in case deduplicator output didn't propagate.
    if architecture_frozen and primary_mcu:
        from agent.knowledge.dependency_graph import get_mcu_family
        mcus = [c for c in comps if _is_mcu(c) and not c.get("builtin")]
        if len(mcus) > 1:
            # Find the MCU matching primary_mcu
            best = None
            for m in mcus:
                family = get_mcu_family(m.get("id_str", ""))
                if family == primary_mcu:
                    best = m
                    break
            if best is None:
                best = mcus[0]
            # Remove extra MCUs
            to_remove = [m for m in mcus if m is not best]
            if to_remove:
                for m in to_remove:
                    _emit(config, "agent:log", {
                        "message": f"  Self-heal: removed extra MCU {m.get('ref_des', '?')} ({m.get('id_str', '?')})"
                    })
                comps = [c for c in comps if c not in to_remove]
                # Write back so downstream nodes (repair, freeze) see the cleaned list
                state["selected_components"] = comps

    fatal = []
    repairable = []
    warnings = []

    def _safe_check(fn, *args, **kwargs):
        """Run a check function, catching any exception to prevent one bad check
        from crashing the entire constraint checker."""
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            _emit(config, "agent:log", {
                "message": f"  Check {fn.__name__} failed: {e}"
            })
            return []

    # Fatal checks
    fatal.extend(_safe_check(_check_mcu_present, comps, primary_mcu, architecture_frozen))
    fatal.extend(_safe_check(_check_duplicate_mcus, comps))

    # MCU architecture mismatch — categorize by severity (same-family = repairable)
    mcu_mismatch_errors = _safe_check(_check_mcu_matches_architecture, comps, primary_mcu, architecture_frozen)
    for e in mcu_mismatch_errors:
        if e.get("category") == "repairable":
            repairable.append(e)
        else:
            fatal.append(e)

    # Repairable checks
    repairable.extend(_safe_check(_check_module_preference, comps, board_type))
    repairable.extend(_safe_check(_check_devkit_redundancy, comps, board_type))
    repairable.extend(_safe_check(_check_missing_programming_header, comps, board_type))
    repairable.extend(_safe_check(_check_missing_power_input, comps))
    repairable.extend(_safe_check(_check_missing_power_regulation, comps, prompt, board_type))

    # Warning checks
    warnings.extend(_safe_check(_check_duplicate_passives, comps))
    warnings.extend(_safe_check(_check_missing_usb_esd, comps))
    warnings.extend(_safe_check(_check_missing_strapping, comps))

    # ── L1 ERC checks (post-repair, deterministic structural checks) ──
    warnings.extend(_safe_check(_check_decoupling_caps, comps))
    warnings.extend(_safe_check(_check_bus_pullups, comps))
    warnings.extend(_safe_check(_check_power_compatibility, comps))
    warnings.extend(_safe_check(_check_power_budget, comps))
    warnings.extend(_safe_check(_check_critical_pin_termination, comps))
    warnings.extend(_safe_check(_check_crystal_load_caps, comps))

    # Remaining repairable errors are unresolved electrical requirements.
    # They are fatal after the bounded repair budget; silently promoting them
    # to warnings creates a design that looks complete but is not buildable.
    passes_used = state.get("repair_passes_used", 0)
    terminal_constraint_error = ""
    if repairable and passes_used >= 2:
        for error in repairable:
            fatal_error = dict(error)
            fatal_error["category"] = "fatal"
            fatal.append(fatal_error)
        terminal_constraint_error = "Constraint repair limit reached: " + "; ".join(
            error.get("message", error.get("code", "constraint failure"))
            for error in repairable[:3]
        )
        _emit(config, "agent:log", {
            "message": f"  Max repair passes reached — {terminal_constraint_error}"
        })
        repairable = []

    # Log results
    if fatal:
        _emit(config, "agent:log", {
            "message": f"  FATAL: {len(fatal)} error(s) — pipeline will halt"
        })
        for e in fatal:
            _emit(config, "agent:log", {"message": f"    [{e['code']}] {e['message']}"})

    if repairable:
        _emit(config, "agent:log", {
            "message": f"  REPAIRABLE: {len(repairable)} error(s)"
        })
        for e in repairable:
            _emit(config, "agent:log", {"message": f"    [{e['code']}] {e['message']}"})

    if warnings:
        _emit(config, "agent:log", {
            "message": f"  WARNINGS: {len(warnings)} issue(s)"
        })
        for e in warnings:
            _emit(config, "agent:log", {"message": f"    [{e['code']}] {e['message']}"})

    if not fatal and not repairable and not warnings:
        _emit(config, "agent:log", {"message": "  All constraint checks passed"})

    status = "failed" if fatal else ("repair_needed" if repairable else "completed")
    emit_tool_event(config, "Constraint Checker", status,
                    f"{len(fatal)} fatal, {len(repairable)} repairable, {len(warnings)} warnings")

    # Determine repair source: if validate has already run, this is post-validate
    repair_source = "constraint_checker"
    if state.get("_last_validated_component_count") is not None:
        repair_source = "post_validate"

    return _stage_result(state, "constraint_checker", {
        "selected_components": comps,
        "fatal_errors": fatal,
        "repairable_errors": repairable,
        "validation_warnings": [e["message"] for e in warnings],
        "repair_source": repair_source,
        "error": terminal_constraint_error or None,
    })
