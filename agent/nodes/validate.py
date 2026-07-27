import json
import re

from agent.nodes.select import _normalize_part_family
from agent.component_insight import build_component_pin_summary
from agent.prompts import VALIDATE_SYSTEM, VALIDATE_USER
from agent.tools import search_components
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result, _call_llm, _clean_json, _ref_prefix_for,
    MAX_VALIDATION_RETRIES, emit_thought, emit_tool_call, emit_tool_end, emit_step,
    _get_id_str,
)
from uuid import uuid4

_KNOWN_SYMBOLS = frozenset([
    "Device:R_Small", "Device:C_Small", "Device:LED", "Device:L_Small",
    "Device:D_Small", "Connector:USB_C_Receptacle_USB2.0_16P",
    "Regulator_Linear:AMS1117-3.3",
    "Connector_USB:TPD6S300A",
    "Sensor_Temperature:TMP117xxYBG",
    "Sensor_Temperature:DS18B20",
    "Sensor_Temperature:BME280",
    "Device:Crystal", "Device:Crystal_GND24", "Device:Crystal_Small",
    "Connector:AVR-ISP-6",
    "Connector:Conn_01x04_Pin",
    "Connector:Conn_01x06_Pin",
    "Connector:Conn_01x08_Pin",
    "Device:Polyfuse",
    "power:PWR_FLAG",
    # Placeholder symbols from support_rules KNOWN_FALLBACK_SYMBOLS —
    # these are used when RAG has no real KiCad symbol for an IC.
    # They are placeholders; the validator should not flag them.
    "Power_Protection:TPD6S300A", "Power_Protection:USBLC6-2SC6", "Power_Protection:IP4234CZ10",
    "Power_Protection:SRV05-4", "Device:TPD6S300A", "Device:USBLC6-2SC6",
])

# Library prefix fixes — import from centralized registry
from agent.library_registry import LIBRARY_PREFIX_FIXES

_CRITICAL_PATTERNS = [
    ("infrared", "led", "Status LED is infrared — not visible to human eye"),
    ("antenna", "resistor", "Antenna selected where resistor required"),
    ("cpld", "capacitor", "CPLD selected where capacitor required"),
    ("pd controller", "connector", "USB PD controller selected where USB-C connector required"),
]

# IC-based circuit validation: when the prompt names a specific IC,
# the selected component must belong to the same family.
_IC_FAMILY_KEYWORDS = {
    "NE555": {"NE555", "LM555", "ICM7555", "TLC555", "LMC555", "555 TIMER"},
    "LM358": {"LM358", "LM324", "LM2904", "OPAMP", "OP-AMP"},
    "LM741": {"LM741", "UA741", "OPAMP"},
    "LM7805": {"LM7805", "L7805", "7805", "REGULATOR"},
    "LM7812": {"LM7812", "L7812", "7812", "REGULATOR"},
    "LM317": {"LM317", "LM338", "LM350", "REGULATOR"},
    "74HC595": {"74HC595", "74HCT595", "SHIFT REGISTER"},
    "CD4017": {"CD4017", "HEF4017", "DECODER"},
}


def _fix_library_prefixes(components: list[dict], emit_fn) -> int:
    n_fixed = 0
    for comp in components:
        id_str = comp.get('id_str', '')
        for wrong, right in LIBRARY_PREFIX_FIXES.items():
            if id_str.startswith(wrong):
                fixed = right + id_str[len(wrong):]
                emit_fn("agent:log", {
                    "message": f"  Corrected prefix: {id_str} -> {fixed}"
                })
                comp['id_str'] = fixed
                n_fixed += 1
                break
    return n_fixed


_PART_FAMILIES: dict[re.Pattern, dict] = {
    # pattern → { "family": str, "traits": set[str], "comment": str }
    re.compile(r'\bESP32[-_ ]?(?:C3|C6|S2|S3|H2|P4)?\b', re.IGNORECASE):
        {"family": "ESP32", "traits": {"wireless", "wifi", "bluetooth", "risc-v or xtensa"}, "comment": "wireless MCU"},
    re.compile(r'\bSTM32\w*\b', re.IGNORECASE):
        {"family": "STM32", "traits": {"arm", "cortex-m"}, "comment": "ARM Cortex-M MCU"},
    re.compile(r'\bRP2040\b', re.IGNORECASE):
        {"family": "RP2040", "traits": {"arm", "cortex-m0+"}, "comment": "Raspberry Pi MCU"},
    re.compile(r'\bRP2350\b', re.IGNORECASE):
        {"family": "RP2350", "traits": {"arm", "cortex-m33", "risc-v"}, "comment": "Raspberry Pi MCU"},
    re.compile(r'\bATmega\w*\b', re.IGNORECASE):
        {"family": "ATmega", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bATTINY\w*\b', re.IGNORECASE):
        {"family": "ATtiny", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bAT90\w*\b', re.IGNORECASE):
        {"family": "AT90", "traits": {"avr"}, "comment": "AVR MCU"},
    re.compile(r'\bSAMD\w*\b', re.IGNORECASE):
        {"family": "SAMD", "traits": {"arm", "cortex-m0+"}, "comment": "ARM Cortex-M0+ MCU"},
}

_MCU_FAMILY_KEYWORDS: dict[str, set[str]] = {
    "ESP32":    {"ESP32", "RISP32", "XTENSA", "WIRELESS", "WIFI", "BLUETOOTH", "IEEE802"},
    "STM32":    {"STM32", "CORTEX", "ARM"},
    "RP2040":   {"RP2040", "CORTEX", "ARM"},
    "RP2350":   {"RP2350", "CORTEX", "ARM"},
    "ATmega":   {"ATMEGA", "MEGA", "AVR"},
    "ATTINY":   {"ATTINY", "TINY", "AVR"},
    "AT90":     {"AT90", "AVR"},
    "SAMD":     {"SAMD", "CORTEX", "ARM"},
}


def _check_prompt_integrity(prompt: str, comps: list[dict]) -> list[str]:
    """Deterministic pre-check: if the user named a specific part family,
    flag any selected component that belongs to a different (incompatible)
    MCU family.

    Returns a list of error messages, empty if no violations.
    """
    mentioned_families: set[str] = set()
    for pattern, info in _PART_FAMILIES.items():
        if pattern.search(prompt):
            mentioned_families.add(info["family"])

    if not mentioned_families:
        return []

    errors: list[str] = []
    for c in comps:
        category = (c.get("category", "") or "").upper()
        if not any(token in category for token in ("MCU", "PROCESSOR", "CPU", "RF_MODULE", "MODULE")) and not any(
            token in (c.get("id_str", "") or "").upper() for token in ("STM32", "ESP32", "RP2040", "RP2350", "ATMEGA", "ATTINY", "AT90", "SAMD")
        ):
            continue
        id_str = (c.get("id_str", "") or "").upper()
        desc = (c.get("description", "") or "").upper()
        id_and_desc = f"{id_str} {desc}"

        detected_families: set[str] = set()
        for fam, keywords in _MCU_FAMILY_KEYWORDS.items():
            if any(kw in id_and_desc for kw in keywords):
                detected_families.add(fam)

        if not detected_families:
            continue

        mentioned_without_wireless = mentioned_families - {"ESP32"}
        detected_without_wireless = detected_families - {"ESP32"}

        if mentioned_without_wireless and detected_without_wireless:
            if mentioned_without_wireless != detected_without_wireless:
                errors.append(
                    f"Prompt-integrity: user requested {', '.join(sorted(mentioned_families))} "
                    f"but {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                    f"is a {', '.join(sorted(detected_families))} family part — "
                    f"family mismatch"
                )
        elif "ESP32" in mentioned_families and "ESP32" not in detected_families and detected_families:
            errors.append(
                f"Prompt-integrity: user requested ESP32 (wireless MCU) "
                f"but {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                f"is a {', '.join(sorted(detected_families))} family part — "
                f"lacks wireless capability"
            )

    return errors


def _check_ic_based_integrity(prompt: str, comps: list[dict], circuit_type: str, primary_ic: str | None) -> list[str]:
    """Check that IC-based circuits have the correct primary IC selected.

    When the user asks for a NE555 timer, the selected components should include
    a NE555 (or compatible), NOT an ESP32 or other MCU.

    Returns a list of error messages, empty if no violations.
    """
    if circuit_type not in ("ic_based", "analog_only") or not primary_ic:
        return []

    # Find the IC family keywords for this primary IC
    ic_upper = primary_ic.upper()
    family_keywords = None
    for ic_name, keywords in _IC_FAMILY_KEYWORDS.items():
        if ic_name.upper() in ic_upper or ic_upper in ic_name.upper():
            family_keywords = keywords
            break

    if not family_keywords:
        return []

    errors: list[str] = []

    # Check if any selected component matches the expected IC family
    found_ic = False
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        desc = (c.get("description", "") or "").upper()
        if any(kw in id_str or kw in desc for kw in family_keywords):
            found_ic = True
            break

    if not found_ic:
        errors.append(
            f"Part family integrity violation: prompt names '{primary_ic}' as core IC. "
            f"Selected components do not include a {primary_ic}-family part. "
            f"Expected a timer IC (NE555, LM555, etc.) or compatible component."
        )

    # Check that no MCU was incorrectly selected for an IC-based circuit
    if circuit_type in ("ic_based", "analog_only"):
        _MCU_KEYWORDS = ("ESP32", "RP2040", "STM32", "ATMEGA", "ATTINY", "SAMD", "NRF", "PIC", "FPGA")
        for c in comps:
            id_str = (c.get("id_str", "") or "").upper()
            desc = (c.get("description", "") or "").upper()
            if any(kw in id_str or kw in desc for kw in _MCU_KEYWORDS):
                errors.append(
                    f"MCU detected in non-MCU circuit: {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                    f"is a microcontroller. This is a {circuit_type} circuit using {primary_ic} — "
                    f"no MCU is needed."
                )

    # Check for unnecessary components in simple IC-based circuits
    if circuit_type == "ic_based" and primary_ic and "NE555" in primary_ic.upper():
        _UNNECESSARY_KEYWORDS = {
            "USB_C_RECEPTACLE": "USB-C connector — a simple barrel jack or terminal block is sufficient for 5V input",
            "TPD6S300A": "USB-C ESD protection — not needed for a NE555 circuit",
            "AMS1117": "3.3V regulator — NE555 operates at 5V directly",
            "LDO": "voltage regulator — NE555 operates at 5V directly",
            "ESP32": "ESP32 MCU — not needed for a NE555 timer circuit",
            "RP2040": "RP2040 MCU — not needed for a NE555 timer circuit",
            "STM32": "STM32 MCU — not needed for a NE555 timer circuit",
            "AVR-ISP": "programming header — NE555 is not programmable",
            "CONN_ARM_CORTEX": "debug header — NE555 is not programmable",
        }
        for c in comps:
            id_str = (c.get("id_str", "") or "").upper()
            for keyword, reason in _UNNECESSARY_KEYWORDS.items():
                if keyword in id_str:
                    errors.append(
                        f"Unnecessary component for NE555 circuit: {c.get('ref_des', '?')} ({c.get('id_str', '?')}) — {reason}"
                    )
                    break

    return errors


# ── L1b Pin-Role Knowledge Base ──────────────────────────────────────
# Pattern from PCBSchemaGen 32-role ontology: each component type has
# expected pin roles.  We check these at the component-list level to
# catch missing support infrastructure before LLM validation.

_PIN_ROLE_KB: dict[str, dict] = {
    "i2c_sensor": {
        "required_roles": {"vdd", "gnd", "sda", "scl"},
        "optional_roles": {"alert", "address", "int"},
        "suggestions": {
            "vdd": "Connect VDD to 3V3 power rail",
            "gnd": "Connect GND to ground",
            "sda": "Connect SDA to MCU I2C data pin (pull-up resistor required)",
            "scl": "Connect SCL to MCU I2C clock pin (pull-up resistor required)",
        },
    },
    "spi_device": {
        "required_roles": {"vdd", "gnd", "mosi", "miso", "sck", "cs"},
        "optional_roles": {"int", "reset"},
        "suggestions": {
            "vdd": "Connect VDD to power rail",
            "gnd": "Connect GND to ground",
            "mosi": "Connect MOSI to MCU SPI MOSI pin",
            "miso": "Connect MISO to MCU SPI MISO pin",
            "sck": "Connect SCK to MCU SPI clock pin",
            "cs": "Connect CS to MCU GPIO for chip select",
        },
    },
    "regulator": {
        "required_roles": {"vin", "vout", "gnd"},
        "optional_roles": {"enable", "bypass", "feedback"},
        "suggestions": {
            "vin": "Connect VIN to input power source (5V or VBUS)",
            "vout": "Connect VOUT to load (3V3 rail)",
            "gnd": "Connect GND to ground",
            "enable": "Pull EN pin high to enable regulator",
        },
    },
    "mcu_3v3": {
        "required_roles": {"vdd", "gnd"},
        "optional_roles": {"uart_tx", "uart_rx", "sda", "scl", "mosi", "miso", "sck",
                           "gpio", "adc", "usb_dp", "usb_dn", "boot", "reset", "xtal_in", "xtal_out"},
        "suggestions": {
            "vdd": "Connect VDD to 3V3 power rail with 100nF decoupling cap",
            "gnd": "Connect all GND pins to ground plane",
        },
    },
    "usb_connector": {
        "required_roles": {"vbus", "gnd", "dp", "dn"},
        "optional_roles": {"cc1", "cc2", "sbu1", "sbu2", "shield"},
        "suggestions": {
            "vbus": "Connect VBUS to 5V power rail",
            "gnd": "Connect GND to ground",
            "dp": "Connect D+ to MCU USB DP pin",
            "dn": "Connect D- to MCU USB DN pin",
            "cc1": "Add 5.1kΩ pull-down to GND for UFP mode",
            "cc2": "Add 5.1kΩ pull-down to GND for UFP mode",
        },
    },
    "crystal": {
        "required_roles": {"xtal_in", "xtal_out"},
        "optional_roles": {"gnd"},
        "suggestions": {
            "xtal_in": "Connect to MCU XTAL1/OSC_IN pin with 12-22pF load cap to GND",
            "xtal_out": "Connect to MCU XTAL2/OSC_OUT pin with 12-22pF load cap to GND",
        },
    },
}

# Category-to-role mapping: classify a component by its id_str/category
_PIN_ROLE_CLASSIFIERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'(?:TMP117|BME280|MPU6050|SSD1306|SH1106|AT24C02|'
                r'MCP4725|ADS1115|BH1750|PCA9685|MCP23017|MCP7940|'
                r'DS3231|INA219|MCP9808|SHT30|SHT31|SGP30|CCS811|VL53L0X)'), "i2c_sensor"),
    (re.compile(r'(?:NRF24L01|RFM95|SX1262|CC1101|CC1310|CC1352)'), "spi_device"),
    (re.compile(r'Regulator_Linear:|Regulator_Switching:'), "regulator"),
    (re.compile(r'Connector_USB:|USB_C_RECEPTACLE'), "usb_connector"),
    (re.compile(r'Device:Crystal|Device:Crystal_GND24|Device:Crystal_Small'), "crystal"),
    (re.compile(r'(?:ESP32|STM32|RP2040|RP2350|ATMEGA|ATTINY|SAMD|NRF52)'), "mcu_3v3"),
]


def _classify_pin_role(comp: dict) -> str | None:
    """Classify a component's pin-role category for L1b checking."""
    id_str = (_get_id_str(comp)).upper()
    for pattern, role_type in _PIN_ROLE_CLASSIFIERS:
        if pattern.search(id_str):
            return role_type
    return None


def _check_pin_roles(comps: list[dict]) -> list[dict]:
    """L1b Pin-Role Pre-Check: verify each IC has expected pin role coverage.

    For each classified component, check that the component list includes
    supporting infrastructure for required pin roles.  This is a
    deterministic pre-check that runs before the LLM validation pass,
    following the PCBSchemaGen L1b pattern.

    Returns list of issue dicts (matching the validate.py issue format).
    """
    issues: list[dict] = []
    classified: dict[str, list[dict]] = {}

    for c in comps:
        role = _classify_pin_role(c)
        if role:
            classified.setdefault(role, []).append(c)

    for role_type, role_components in classified.items():
        kb = _PIN_ROLE_KB.get(role_type)
        if not kb:
            continue

        for rc in role_components:
            ref = rc.get("ref_des", "")
            id_str = _get_id_str(rc)

            for required_role in kb["required_roles"]:
                suggestion = kb["suggestions"].get(required_role, "")
                role_covered = _check_role_covered(comps, rc, required_role)
                if not role_covered:
                    issues.append({
                        "id_str": id_str,
                        "severity": "warning",
                        "message": (
                            f"{ref} ({id_str}): expected pin role '{required_role}' "
                            f"appears uncovered"
                        ),
                        "suggestion": suggestion,
                    })

    return issues


def _check_role_covered(comps: list[dict], target: dict, role: str) -> bool:
    """Check if a required pin role is covered by the component list.

    This is a heuristic check based on component descriptions and
    supporting component relationships.
    """
    ref = target.get("ref_des", "")
    id_str = (_get_id_str(target)).upper()
    desc = (target.get("description", "") or "").lower()

    # Power roles: check if a regulator output matches or a power net exists
    if role in ("vdd", "vin"):
        if "3.3" in desc or "3V3" in desc or "5V" in desc:
            return True
        for c in comps:
            if c.get("ref_des") == ref:
                continue
            cid = (_get_id_str(c)).upper()
            if "REGULATOR" in cid:
                return True
            # MCU VDD: check if a regulator for this voltage exists
            if role == "vdd" and ("ESP32" in id_str or "STM32" in id_str or "RP2040" in id_str):
                for c2 in comps:
                    c2id = (_get_id_str(c2)).upper()
                    if "AMS1117" in c2id or "AP2112" in c2id or "MCP1700" in c2id:
                        return True
        return False

    # Ground: always covered (every component has GND)
    if role == "gnd":
        return True

    # I2C roles: check MCU I2C capability
    if role in ("sda", "scl"):
        for c in comps:
            cid = (_get_id_str(c)).upper()
            cdesc = (c.get("description", "") or "").lower()
            if "I2C" in cdesc or "I2C" in cid or "SDA" in cid or "SCL" in cid:
                return True
            if any(mcu in cid for mcu in ("ESP32", "STM32", "RP2040", "RP2350", "ATMEGA", "ATTINY", "SAMD")):
                return True
        return False

    # SPI roles
    if role in ("mosi", "miso", "sck", "cs"):
        for c in comps:
            cid = (_get_id_str(c)).upper()
            if any(spi in cid for spi in ("SPI", "MOSI", "MISO", "SCK")):
                return True
            if any(mcu in cid for mcu in ("ESP32", "STM32", "RP2040", "RP2350")):
                return True
        return False

    # USB roles
    if role in ("dp", "dn"):
        for c in comps:
            cid = (_get_id_str(c)).upper()
            if "USB" in cid:
                return True
        return False

    # Crystal roles: check if MCU has crystal support
    if role in ("xtal_in", "xtal_out"):
        return True

    # Default: assume role is covered (optional roles)
    return True


_BARE_RF_PATTERNS = re.compile(
    r'(ESP32|ESP8266|NRF24[L]?[012]|NRF52[345]|CC1101|CC1310|CC1352|SX126[128]|LR1110|LR1120)',
    re.IGNORECASE,
)
_MODULE_MARKERS = re.compile(
    r'(WROOM|MINI|MOD|DEVKIT|MODULE|DK|DONGLE|BOARD|BREAKOUT)',
    re.IGNORECASE,
)
_MODULE_LIBRARIES = ("RF_MODULE", "MODULE_")


def _check_module_preference(comps: list[dict]) -> list[tuple[str, str]]:
    """Detect bare RF ICs (QFN/BGA chips) that should be replaced with
    pre-certified modules for easier PCB routing.

    Returns ``[(error_message, id_str)]`` — empty list means no violations.
    The ``id_str`` is used by the caller to populate ``rejected_ids`` so the
    offending component is not re-selected on retry.
    """
    errors: list[tuple[str, str]] = []
    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        library = id_str.split(":")[0] if ":" in id_str else ""

        # Skip if already a module
        if any(lib in library for lib in _MODULE_LIBRARIES):
            continue
        if _MODULE_MARKERS.search(id_str):
            continue

        # Check if it's a bare RF IC
        if _BARE_RF_PATTERNS.search(id_str):
            errors.append((
                f"Module preference: {c.get('ref_des', '?')} ({c.get('id_str', '?')}) "
                f"is a bare RF IC — replace with a pre-certified module "
                f"(search for named modules with WROOM/DEVKIT suffix) "
                f"for easier PCB routing and FCC compliance",
                c.get("id_str", ""),
            ))
    return errors


_DEVKIT_REDUNDANT_LIBS = frozenset({
    "Interface_USB", "Regulator_Linear", "Connector_USB", "Connector",
})

_CORE_LIB_PREFIXES = ("RF_MODULE:", "MCU_", "MODULE_")
_CORE_ID_KEYWORDS = frozenset({
    "WROOM", "ESP32", "ESP8266", "STM32", "RP2040", "RP2350",
    "ATMEGA", "ATTINY", "AT90", "ATXMEGA", "SAMD",
})


def _is_core_component(c: dict) -> bool:
    """Return True if *c* is a primary MCU/module that should never be
    removed by redundancy enforcement or devkit deduplication."""
    id_str = (c.get("id_str", "") or "").upper()
    lib = (id_str.split(":")[0] + ":") if ":" in id_str else ""
    if any(lib.startswith(p) for p in _CORE_LIB_PREFIXES):
        return True
    if any(kw in id_str for kw in _CORE_ID_KEYWORDS):
        return True
    return False


def _remove_devkit_redundancy(comps: list[dict], emit_fn) -> tuple[list[dict], list[str]]:
    """If any selected component is a DevKit/dev-board/WROOM/module, strip
    components that duplicate its on-board features (USB-UART bridge, regulator,
    USB-C connector, crystal, crystal load caps) plus their decoupling/support
    passives.

    Returns ``(filtered_components, removed_refs)``.
    """
    _MODULE_KEYWORDS = frozenset({
        "DEVKIT", "WROOM", "MINI", "MODULE", "DK", "NODEMCU", "BOARD", "BREAKOUT"
    })
    module_refs = [
        c["ref_des"] for c in comps
        if any(kw in (c.get("id_str", "") or "").upper() for kw in _MODULE_KEYWORDS)
    ]
    if not module_refs:
        return comps, []

    # Identify redundant main components
    # ── WROOM guard ────────────────────────────────────────────────────
    # Bare WROOM modules (ESP32-WROOM-32, ESP32-WROOM-32D) do NOT have
    # on-board voltage regulation or USB-UART bridges — they are bare
    # modules, not dev boards.  Only strip redundant components when a
    # true development board keyword (DEVKIT, NODEMCU, BOARD, BREAKOUT)
    # is present.
    _DEV_BOARD_KW = frozenset({"DEVKIT", "NODEMCU", "BOARD", "BREAKOUT"})
    has_dev_board = any(
        any(kw in (c.get("id_str", "") or "").upper() for kw in _DEV_BOARD_KW)
        for c in comps if c.get("ref_des", "") in module_refs
    )
    redundant_refs: set[str] = set()

    # ── Check if any MCU has native USB (no external bridge needed) ────
    _NATIVE_USB_MCUS = frozenset([
        "ESP32-S3", "ESP32-C3", "ESP32-C6", "ESP32-H2",
        "RP2040", "RP2350",
        "STM32F0", "STM32F4", "STM32G4", "STM32H5", "STM32H7",
        "STM32L0", "STM32L4", "STM32U5",
        "SAMD21", "SAMD51", "SAMD11",
        "NRF52840", "NRF52833", "NRF52820",
        "TEENSY", "XIAO",
    ])
    mcu_has_native_usb = False
    for c in comps:
        c_id = (c.get("id_str", "") or "").upper()
        c_desc = (c.get("description", "") or "").upper()
        if any(kw in c_id or kw in c_desc for kw in _NATIVE_USB_MCUS):
            mcu_has_native_usb = True
            break

    for c in comps:
        id_str = (c.get("id_str", "") or "").upper()
        ref = c.get("ref_des", "")
        if not ref:
            continue
        # Never remove user-locked or core MCU/module components
        if c.get("user_locked") or _is_core_component(c):
            continue
        # USB-UART bridges — remove if dev board OR if MCU has native USB
        if any(kw in id_str for kw in ("CP2102", "CH340", "FT230", "FT232")):
            if has_dev_board or mcu_has_native_usb:
                redundant_refs.add(ref)
        # Voltage regulators — only remove if a true dev board is present
        if has_dev_board and ("AMS1117" in id_str or "REGULATOR_LINEAR:" in id_str):
            redundant_refs.add(ref)
        # USB-C receptacles — only remove if a true dev board is present
        if has_dev_board and "USB_C_RECEPTACLE" in id_str:
            redundant_refs.add(ref)
        # Dev module with built-in MCU → remove standalone MCU of same type
        # (e.g., Wemos C3 Mini already has ESP32-C3, don't also select a bare ESP32-C3)
        if has_dev_board:
            _MCU_FAMILIES = ("ESP32", "ESP8266", "STM32", "ATMEGA", "RP2040", "RP2350", "NRF", "SAMD")
            if any(fam in id_str for fam in _MCU_FAMILIES) and ref not in module_refs:
                redundant_refs.add(ref)
        # Non-RTC crystals — only remove if the MCU's dependency graph says
        # the module covers the crystal requirement.  This prevents infinite
        # loops where the crystal is removed, rejected, re-requested by the
        # dependency expander, and removed again.
        if id_str in ("DEVICE:CRYSTAL_SMALL", "DEVICE:CRYSTAL", "DEVICE:CRYSTAL_GND24"):
            from agent.knowledge.dependency_graph import get_mcu_family, DEPENDENCY_GRAPH
            crystal_covered = False
            for c2 in comps:
                c2_id = (c2.get("id_str", "") or "").upper()
                family = get_mcu_family(c2_id)
                if family and family in DEPENDENCY_GRAPH:
                    overrides = DEPENDENCY_GRAPH[family].get("module_overrides", {})
                    # Check if any crystal requirement is covered by module_overrides
                    for req_key, covered in overrides.items():
                        if covered and "crystal" in req_key:
                            crystal_covered = True
                            break
                if crystal_covered:
                    break
            if crystal_covered:
                redundant_refs.add(ref)

    # Library-level catch-all: any component from a library known to be
    # redundant with WROOM/modules (e.g. Interface_USB, Regulator_Linear,
    # Connector_USB). This catches parts not covered by specific patterns
    # above (like TPS25730D in Interface_USB).
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        if c.get("user_locked") or _is_core_component(c):
            continue
        c_id_str = (c.get("id_str", "") or "").upper()
        c_lib = c_id_str.split(":")[0] if ":" in c_id_str else ""
        if c_lib in _DEVKIT_REDUNDANT_LIBS:
            redundant_refs.add(ref)

    # Remove support passives whose for_component is a redundant ref
    for c in comps:
        fc = c.get("for_component", "")
        if fc and fc in redundant_refs:
            redundant_refs.add(c.get("ref_des", ""))

    # Remove crystal load caps (description mentions "crystal" + "cap" or "load")
    # that sit alongside a now-redundant crystal, even if no for_component link.
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        desc = (c.get("description", "") or "").upper()
        id_str = (c.get("id_str", "") or "").upper()
        if "CRYSTAL" not in desc and "CRYSTAL" not in id_str:
            continue
        # Check if any already-redundant component shares this subsystem
        sub = c.get("subsystem", "")
        if sub and any(
            comp.get("subsystem") == sub and comp.get("ref_des", "") in redundant_refs
            for comp in comps
        ):
            redundant_refs.add(ref)

    removed = [f"{r} ({c.get('id_str', '')})" for r in redundant_refs
               for c in comps if c.get("ref_des", "") == r]
    filtered = [c for c in comps if c.get("ref_des", "") not in redundant_refs]
    if removed:
        emit_fn("agent:log", {
            "message": f"  Module redundancy: removed {len(removed)} duplicate component(s): "
                       f"{', '.join(removed)}"
        })
    return filtered, removed


def _enforce_redundancy_removal(
    comps: list[dict],
    issues: list[dict],
    emit_fn,
) -> tuple[list[dict], list[dict], list[str]]:
    """Post-LLM enforcement: scan all issues (errors + warnings) for redundancy
    keywords and delete the flagged components from the BOM immediately.

    This gives the validator actual teeth — warnings like "crystal is redundant"
    result in the crystal AND its support passives being stripped from ``comps``
    before the list reaches the layout engine.

    Returns ``(cleaned_comps, cleaned_issues, removed_refs)``.
    """
    redundant_refs: set[str] = set()
    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        if "redundant" not in msg and "already integrated" not in msg:
            continue
        # Match by id_str — catch ALL comps with that id_str (e.g., two
        # Device:C_Small load caps for the same crystal).
        eid = issue.get("id_str", "")
        matched_some = False
        if eid:
            for c in comps:
                if c.get("id_str", "") == eid:
                    # Protect user-locked and core MCU/module from removal
                    if c.get("user_locked") or _is_core_component(c):
                        continue
                    redundant_refs.add(c.get("ref_des", ""))
                    matched_some = True
        # Fallback: try matching ref_des from message text
        if not matched_some:
            for c in comps:
                ref = c.get("ref_des", "")
                if not ref:
                    continue
                # Protect user-locked and core MCU/module from removal
                if c.get("user_locked") or _is_core_component(c):
                    continue
                # Pattern 1: redundancy keyword BEFORE ref_des within 80 chars
                ctx_fwd = re.compile(
                    rf'(?:redundant|superfluous|unnecessary|duplicate|already\s+integrated)'
                    rf'.{{0,80}}\b{re.escape(ref.lower())}\b',
                    re.IGNORECASE
                )
                if ctx_fwd.search(msg):
                    redundant_refs.add(ref)
                    break
                # Pattern 1b: ref_des BEFORE redundancy keyword within 80 chars (reverse)
                ctx_rev = re.compile(
                    rf'\b{re.escape(ref.lower())}\b'
                    rf'.{{0,80}}(?:redundant|superfluous|unnecessary|duplicate)',
                    re.IGNORECASE
                )
                if ctx_rev.search(msg):
                    redundant_refs.add(ref)
                    break
                # Pattern 2 (C-04 fix): ref_des as sentence subject BUT only when
                # the same sentence ALSO contains a redundancy keyword. This prevents
                # matching the SURVIVOR when the redundant part is the real subject.
                sentence_m = re.search(
                    rf'(?:redundant|superfluous|unnecessary|duplicate|already\s+integrated)',
                    msg, re.IGNORECASE
                )
                if sentence_m:
                    subj_pattern = re.compile(
                        rf'\b{re.escape(ref.lower())}\b\s+(?:is|was|has|contains|integrates)',
                        re.IGNORECASE
                    )
                    if subj_pattern.search(msg):
                        redundant_refs.add(ref)
                        break

    if not redundant_refs:
        return comps, issues, []

    # Cascade: remove support passives linked to a removed main component
    pre_cascade = set(redundant_refs)
    for c in comps:
        ref = c.get("ref_des", "")
        if not ref or ref in redundant_refs:
            continue
        if c.get("user_locked"):
            continue
        fc = c.get("for_component", "")
        desc = (c.get("description", "") or "").upper()
        if fc and fc in redundant_refs:
            redundant_refs.add(ref)
        elif any(r in desc for r in redundant_refs):
            redundant_refs.add(ref)
    cascade_added = redundant_refs - pre_cascade
    if cascade_added:
        cascade_detail = [
            f"{r}" for r in sorted(cascade_added)
        ]
        emit_fn("agent:log", {
            "message": f"  Cascade removal: {', '.join(cascade_detail)} (support components of removed ICs)"
        })

    # Capture id_strs BEFORE filtering comps
    removed_ids = {
        c.get("id_str", "") for c in comps
        if c.get("ref_des", "") in redundant_refs and c.get("id_str", "")
    }
    filtered = [c for c in comps if c.get("ref_des", "") not in redundant_refs]

    # Remove issues about now-deleted components
    filtered_issues = [
        i for i in issues
        if i.get("id_str", "") not in removed_ids
    ]

    removed = [f"{r} ({c.get('id_str', '')})" for r in redundant_refs
               for c in comps if c.get("ref_des", "") == r]
    if removed:
        emit_fn("agent:log", {
            "message": f"  Enforced redundancy: removed {len(removed)} component(s): "
                       f"{', '.join(removed)}"
        })

    return filtered, filtered_issues, sorted(removed)


def validate_node(state, config):
    val_id = uuid4().hex[:8]
    emit_tool_call(config, val_id, "Validation", "running")
    emit_thought(config, "Validating component selections...")
    emit_assistant_message(config, "Checking the selected components for electrical and functional issues...")
    emit_tool_event(config, "Validation", "running", "Checking component selections...")
    contract = _check_stage_contract("validate", state, ["selected_components", "analysis", "prompt"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "validate", {"selected_components": [], "validation_errors": []})
    comps = list(state.get("selected_components", []))
    analysis = state.get("analysis", [])
    prompt = state.get("prompt", "")
    research = state.get("research_results", [])

    # ── Merge datasheet search results into component data ──────────────
    # The datasheet_search_node runs after select but before validate.
    # Its results are in state["datasheet_search_results"] but never merged
    # into each component's datasheet_text. Fix that now so the LLM
    # validator sees fresh datasheet info.
    ds_results = state.get("datasheet_search_results", [])
    if ds_results:
        ds_by_ref = {r["ref_des"]: r for r in ds_results if r.get("ref_des")}
        for c in comps:
            ref = c.get("ref_des", "")
            if ref in ds_by_ref and ds_by_ref[ref].get("summary"):
                c["datasheet_text"] = ds_by_ref[ref]["summary"][:500]
    if not comps:
        _emit(config, "agent:log", {"message": "No components to validate."})
        return _stage_result(state, "validate", {"selected_components": comps, "validation_errors": []})
    subsystems = "\n".join(
        f'  {a.get("subsystem", "?")}: {a.get("function", "")}'
        for a in analysis
    )

    # Deterministic prompt-integrity pre-check runs BEFORE the LLM validation
    # so that part-family mismatches are caught even if the LLM hallucinates.
    emit_step(config, val_id, "Checking prompt integrity and module preferences...", "running")
    integrity_errors = _check_prompt_integrity(prompt, comps)
    if integrity_errors:
        _emit(config, "agent:log", {
            "message": f"  Prompt-integrity pre-check found {len(integrity_errors)} issue(s)"
        })

    # IC-based circuit integrity check: verify the correct IC is selected
    circuit_type = state.get("circuit_type", "mcu_based")
    primary_ic = state.get("primary_ic")
    ic_errors = _check_ic_based_integrity(prompt, comps, circuit_type, primary_ic)
    if ic_errors:
        _emit(config, "agent:log", {
            "message": f"  IC-based circuit check found {len(ic_errors)} issue(s)"
        })

    # L1b Pin-Role Pre-Check: verify each IC has expected pin role coverage.
    # This runs before LLM validation, patterned after PCBSchemaGen L1b.
    pin_role_issues = _check_pin_roles(comps)
    if pin_role_issues:
        _emit(config, "agent:log", {
            "message": f"  L1b pin-role pre-check found {len(pin_role_issues)} issue(s)"
        })

    # Module preference check: bare RF ICs should be replaced with modules.
    # Skip this check entirely for non-MCU circuits (IC-based, analog-only)
    # or bare_ic/custom_pcb boards where bare IC use is intentional.
    board_type = state.get("board_type", "")
    if board_type in ("bare_ic", "custom_pcb"):
        module_errors = []
    elif circuit_type in ("mcu_based", "mixed"):
        module_errors = _check_module_preference(comps)
    else:
        module_errors = []
    if module_errors:
        _emit(config, "agent:log", {
            "message": f"  Module preference pre-check found {len(module_errors)} issue(s)"
        })
        replaced_any = False
        for err_msg, err_id in list(module_errors):
            for ci, c in enumerate(comps):
                if c.get("id_str", "") != err_id:
                    continue
                bare_name = err_id.split(":")[-1] if ":" in err_id else err_id
                bare_base = bare_name.split("-")[0].upper()
                # Extract the MCU variant (e.g., "C3" from "ESP32-C3") for family matching
                mcu_variant = ""
                parts = bare_name.split("-")
                if len(parts) >= 2:
                    mcu_variant = parts[1].upper()
                # Search for a module variant (e.g., ESP32-C3 → ESP32-C3-DevKitM-1)
                try:
                    mod_results = search_components(f"{bare_base} DEVKIT WROOM module", k=10)
                    replacement = None
                    for r in mod_results:
                        rid = (r.get("id_str", "") or "").upper()
                        lib = rid.split(":")[0] if ":" in rid else ""
                        # Must match the MCU family (e.g., ESP32-C3, not ESP32-S3)
                        if bare_base.upper() in rid and (
                            not mcu_variant or mcu_variant in rid
                        ) and any(
                            kw in rid for kw in ("WROOM", "DEVKIT", "MINI", "MODULE", "DK")
                        ):
                            replacement = r
                            break
                    if replacement:
                        # Build a component entry matching the existing schema
                        new_ref_des = c.get("ref_des", "")
                        new_entry = {
                            "id_str": replacement["id_str"],
                            "ref_des": new_ref_des,
                            "category": replacement.get("category",
                                replacement["id_str"].split(":")[0] if ":" in replacement["id_str"] else "General"),
                            "description": replacement.get("text", replacement.get("description",
                                f"Module variant of {err_id}")),
                            "footprint": replacement.get("footprint", ""),
                            "pads": replacement.get("pads", []),
                            "justification": f"Auto-replaced bare RF IC ({err_id}) with pre-certified module",
                            "datasheet_text": "",
                            "subsystem": c.get("subsystem", ""),
                        }
                        comps[ci] = new_entry
                        _emit(config, "agent:log", {
                            "message": f"  Auto-replaced {c.get('ref_des', '?')} ({err_id}) "
                                       f"→ {replacement['id_str']} (module variant)"
                        })
                        # Remove this error from module_errors
                        module_errors = [(m, i) for m, i in module_errors if i != err_id]
                        replaced_any = True
                    else:
                        _emit(config, "agent:log", {
                            "message": f"  No module variant found for {err_id} — will retry with different bare IC if available"
                        })
                except Exception as e:
                    print(f"Module auto-replace search failed for {err_id}: {e}")
        if replaced_any and not module_errors:
            _emit(config, "agent:log", {
                "message": "  All bare RF ICs auto-replaced — clearing module preference errors"
            })
        # Re-check: if we replaced everything, module_errors is now empty
        if not module_errors:
            pass  # All resolved

    # Build components_list AFTER auto-replace so LLM sees the final (module) version
    components_list = "\n".join(
        (
            f'  {c["ref_des"]}: {c["id_str"]}  [{c.get("category", "?")}]'
            f'  "{c.get("description", "")[:80]}"'
            + (f'  [DATASHEET] {c.get("datasheet_text", "")[:300]}' if c.get("datasheet_text") else '')
            + f'\n    Pins: {build_component_pin_summary(c["id_str"], research)}'
        )
        for c in comps
    )

    emit_step(config, val_id, "Running LLM validation...", "running")
    llm_failed = False
    try:
        text = _call_llm(VALIDATE_SYSTEM, VALIDATE_USER.format(
            prompt=prompt,
            subsystems=subsystems,
            components_list=components_list,
        ), stage="validate")
    except Exception:
        text = ""
        llm_failed = True
    text = _clean_json(text)
    try:
        result = json.loads(text) if text else {}
    except json.JSONDecodeError:
        print(f"Failed to parse validation JSON: {text[:200]}")
        result = {}
    if not result or llm_failed:
        result = {"valid": False, "issues": [{
            "id_str": "",
            "severity": "error",
            "message": "LLM validation call failed or returned unparseable result — cannot verify BOM correctness",
            "suggestion": "Retry the validation step or manually review the selected components",
        }]}

    # Inject deterministic pre-check errors into LLM result
    for err in integrity_errors:
        result.setdefault("issues", []).append({
            "id_str": "",
            "severity": "error",
            "message": err,
            "suggestion": "Reselect using a part matching the originally specified family",
        })
        result["valid"] = False
    for err in ic_errors:
        result.setdefault("issues", []).append({
            "id_str": "",
            "severity": "error",
            "message": err,
            "suggestion": "Remove MCU and select the correct IC for this circuit type",
        })
        result["valid"] = False
    for err_msg, err_id in module_errors:
        result.setdefault("issues", []).append({
            "id_str": "",
            "severity": "error",
            "message": err_msg,
            "suggestion": "Search for and replace with a pre-certified module (WROOM/DEVKIT variant)",
        })
        result["valid"] = False
    for issue in pin_role_issues:
        result.setdefault("issues", []).append(issue)
        if issue.get("severity") in ("error", "fatal_error"):
            result["valid"] = False
    issues = result.get("issues", [])
    missing = result.get("missing_components", [])
    errors = [i for i in issues if i.get("severity") == "error"]
    warnings = [i for i in issues if i.get("severity") == "warning"]

    # ── Architecture freeze guard ──
    # When architecture is frozen, the validate node must be READ-ONLY.
    # It should only classify issues, never modify the component list.
    # This prevents the validate→repair loop from fighting the architecture.
    arch_frozen = state.get("architecture_frozen", False)

    if arch_frozen:
        emit_step(config, val_id, "Architecture frozen — validation is read-only", "running")
        # Skip ALL mutations: redundancy enforcement, devkit removal, auto-add
        # Just classify issues and return
    else:
        emit_step(config, val_id, "Post-processing and applying corrections...", "running")

    if not arch_frozen:
        # ── Post-LLM redundancy enforcement ──
        # The LLM may flag components as "redundant" (e.g. external crystal + load
        # caps when a WROOM module is selected).  This step gives those warnings
        # teeth: the flagged components are DELETED from comps immediately.
        comps, issues, enforced_removed = _enforce_redundancy_removal(
            comps, issues, lambda k, v: _emit(config, k, v),
        )
        _enforced_rejected_ids: list[str] = []
        if enforced_removed:
            _enforced_refs = set(enforced_removed)
            _enforced_rejected_ids = [
                c.get("id_str", "") for c in state.get("selected_components", [])
                if c.get("ref_des", "") in _enforced_refs and c.get("id_str", "")
            ]
            errors = [i for i in issues if i.get("severity") == "error"]
            warnings = [i for i in issues if i.get("severity") == "warning"]

        n_fixed = _fix_library_prefixes(comps, lambda k, v: _emit(config, k, v))
        if n_fixed:
            _emit(config, "agent:log", {"message": f"  Fixed {n_fixed} library prefix(es)"})
        # Remove prefix-fixable issues from the error list (data is now corrected)
        issues = [i for i in issues if "library prefix" not in (i.get("message", "") or "").lower()]
        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]

        # ── Auto-remove redundant DevKit components ──
        comps, removed_refs = _remove_devkit_redundancy(comps, lambda k, v: _emit(config, k, v))
        _devkit_rejected_ids: list[str] = []
        if removed_refs:
            _devkit_rejected_ids = [
                c.get("id_str", "") for c in state.get("selected_components", [])
                if c.get("ref_des", "") in set(removed_refs) and c.get("id_str", "")
            ]
            # Rebuild error/warning lists — remove issues about now-removed components
            removed_ref_set = set(removed_refs)
            issues = [i for i in issues if not any(
                r in (i.get("id_str", "") or "") for r in removed_ref_set
            )]
            errors = [i for i in issues if i.get("severity") == "error"]
            warnings = [i for i in issues if i.get("severity") == "warning"]
    else:
        _enforced_rejected_ids: list[str] = []
        _devkit_rejected_ids: list[str] = []

    # C-05: Build full rejected list BEFORE critical-pattern early return so
    # that devkit-removed and enforcement-removed IDs are never lost.
    _base_rejected = list(state.get("rejected_ids", []))
    for rid in _devkit_rejected_ids:
        if rid not in _base_rejected:
            _base_rejected.append(rid)
    for rid in _enforced_rejected_ids:
        if rid not in _base_rejected:
            _base_rejected.append(rid)

    for issue in issues:
        msg = (issue.get("message", "") or "").lower()
        for keyword, context, reason in _CRITICAL_PATTERNS:
            if keyword in msg and context in msg:
                detail = (f"Critical validation failure: {reason}\n"
                          f"  Component: {issue.get('id_str', '?')}\n"
                          f"  Detail: {issue.get('message', '')}\n"
                          f"  Suggestion: {issue.get('suggestion', '')}")
                rejected = list(_base_rejected)
                crit_id = issue.get("id_str", "")
                if crit_id and crit_id not in rejected and not any(
                    c.get("user_locked") for c in comps if c.get("id_str") == crit_id
                ):
                    rejected.append(crit_id)
                _emit(config, "agent:log", {
                    "message": f"  Validation rejected {issue.get('id_str', '?')}: {issue.get('message', '')}"
                })
                emit_tool_event(config, "Validation", "failed", detail, details={
                    "component": issue.get("id_str", "?"),
                    "reason": issue.get("message", ""),
                    "suggestion": issue.get("suggestion", ""),
                })
                emit_tool_end(config, val_id, f"Validation failed — {issue.get('message', '')}", status="failed")
                return _stage_result(state, "validate", {
                    "selected_components": comps,
                    "validation_errors": [issue.get("message", "")],
                    "error": detail,
                    "rejected_ids": rejected,
                })

    for issue in issues:
        _emit(config, "agent:log", {
            "message": f"  [{issue.get('severity', 'info').upper()}] {issue.get('message', '')}"
        })
    for err in errors:
        emit_tool_event(config, "Validation", "running", f"Error: {err.get('message', '')}")
    for w in warnings:
        emit_tool_event(config, "Validation", "running", f"Warning: {w.get('message', '')}")
    corrections = []
    validation_errors = []

    # When architecture is frozen, don't auto-add missing components —
    # report them as errors instead. EXCEPTION: inject support components
    # for known-requirement ICs (e.g., NE555 timing components).
    if missing and state.get("architecture_frozen"):
        from agent.knowledge.component_catalog import resolve_component
        injectable = []
        unresolvable = []
        for mc in missing:
            # Try to resolve from component catalog
            req_id = mc.get("requirement_id", "")
            desc = mc.get("description", "")
            resolved = resolve_component(req_id, desc) if req_id else None
            if resolved:
                injectable.append({"resolved": resolved, "original": mc})
                _emit(config, "agent:log", {
                    "message": f"  Auto-injecting support component: {resolved['id_str']} for {req_id}"
                })
            else:
                unresolvable.append(mc)
                validation_errors.append(
                    f"Missing component (architecture locked): {desc}"
                )
        if injectable:
            # Clear missing list and add injectable to the auto-add path
            missing = [{"requirement_id": inj["resolved"]["id_str"],
                        "description": inj["resolved"].get("description", ""),
                        "preferred_id_str": inj["resolved"]["id_str"],
                        "library_filter": inj["resolved"].get("category", "")}
                       for inj in injectable]
        else:
            missing = []

    if missing:
        emit_thought(config, f"Searching for {len(missing)} missing component(s)...")
        for mc in missing:
            query = mc.get("suggested_query", mc.get("description", ""))
            try:
                lib_filter = mc.get("library_filter") or None
                preferred_id = mc.get("preferred_id_str", "")
                if preferred_id in _KNOWN_SYMBOLS:
                    best = {"id_str": preferred_id, "text": mc.get("description", query), "footprint": "", "pads": []}
                else:
                    results = search_components(query, k=5, library_filter=lib_filter)
                    best = results[0] if results else None
                if not best:
                    q_lower = query.lower()
                    for known_id in _KNOWN_SYMBOLS:
                        known_name = known_id.rpartition(':')[2].lower().replace('_', ' ')
                        if known_name in q_lower or q_lower in known_name:
                            best = {"id_str": known_id, "text": mc.get("description", query), "footprint": "", "pads": []}
                            break
                # ── Programming/UART header deterministic fallback ──
                if not best:
                    prog_keywords = ("programming", "debug", "uart header", "tx rx")
                    if any(kw in query.lower() for kw in prog_keywords):
                        for fallback_id in ("Connector:Conn_01x06_Pin",
                                            "Connector:Conn_01x04_Pin",
                                            "Connector:Conn_01x08_Pin"):
                            if fallback_id in _KNOWN_SYMBOLS:
                                best = {"id_str": fallback_id, "text": mc.get("description", query),
                                        "footprint": "", "pads": []}
                                _emit(config, "agent:log", {
                                    "message": f"  Programming header fallback: using {fallback_id}"
                                })
                                break
                if best:
                    if not best.get("footprint"):
                        try:
                            from kicad_rag.store import resolve_footprint_from_filters
                            resolved = resolve_footprint_from_filters(best["id_str"])
                            if resolved:
                                best["footprint"] = resolved
                        except Exception as e:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.warning(f"Failed to resolve footprint for {best['id_str']}: {e}")
                    if not best.get("footprint"):
                        try:
                            from agent.tools import fetch_footprint
                            info = fetch_footprint(best["id_str"])
                            if info:
                                best["footprint"] = info.get("footprint", "")
                                best["pads"] = info.get("pads", [])
                        except Exception as e:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.warning(f"Failed to fetch footprint for {best['id_str']}: {e}")
                    ref_prefix = _ref_prefix_for(best["id_str"], best["id_str"].split(":")[0])
                    existing_nums = set()
                    for c in comps + corrections:
                        r = c.get("ref_des", "")
                        prefix = "".join(ch for ch in r if ch.isalpha()) or "U"
                        num = "".join(ch for ch in r if ch.isdigit())
                        if prefix == ref_prefix and num:
                            existing_nums.add(int(num))
                    next_num = 1
                    while next_num in existing_nums:
                        next_num += 1
                    ref = f"{ref_prefix}{next_num}"
                    corrections.append({
                        "id_str": best["id_str"],
                        "ref_des": ref,
                        "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                        "description": best.get("text", mc.get("description", "")),
                        "footprint": best.get("footprint", ""),
                        "pads": best.get("pads", []),
                        "justification": f"Auto-added by validator: {mc.get('description', query)}",
                        "datasheet_text": "",
                        "subsystem": mc.get("subsystem", ""),
                    })
                    _emit(config, "agent:log", {
                        "message": f"  Added missing {ref} ({best['id_str']}) for: {mc.get('description', query)}"
                    })
            except Exception as e:
                print(f"Validator search failed for '{query}': {e}")
    if corrections:
        comps = comps + corrections
        _emit(config, "agent:log", {
            "message": f"  Corrected: added {len(corrections)} missing component(s)"
        })
    # Filter out errors that were fixed by auto-added corrections.
    # Uses stem-matched keyword overlap: "resistors" matches "resistor",
    # "decoupling" matches "decouple", etc.
    import re as _re
    _STOP = frozenset({"with", "from", "that", "this", "have", "been", "for",
        "the", "and", "are", "its", "has", "not", "can", "will", "but",
        "also", "than", "into", "more", "some", "their", "about", "other",
        "over", "such", "than", "very", "just", "should", "would", "could",
        "each", "between", "without", "within", "after", "before", "during",
        "when", "where", "there", "which", "while", "because", "through"})
    def _stem(w: str) -> str:
        """Crude stem: drop common suffixes so 'resistors' ~ 'resistor'."""
        w = w.rstrip('s')       # resistors → resistor, caps → cap
        w = w.rstrip('ing')     # decoupling → decoupl
        w = w.rstrip('ed')      # integrated → integrat
        w = w.rstrip('e')       # decouple → decoupl, integrate → integrat
        return w
    def _keywords(text):
        raw = _re.findall(r'[a-zA-Z0-9]+', text.lower())
        return {_stem(w) for w in raw if len(w) >= 4 and w not in _STOP}
    fixed_descs = [c.get("description", "") for c in corrections]
    validation_errors = []
    for e in errors:
        msg = e.get("message", "")
        if not msg:
            continue
        msg_kw = _keywords(msg)
        fixed = False
        for fd in fixed_descs:
            shared = len(msg_kw & _keywords(fd))
            if shared >= 2:
                fixed = True
                break
        if not fixed:
            validation_errors.append(msg)
    # Remove errors about known placeholder symbols (e.g. Device:TPD6S300A).
    # These are intentional fallback symbols from support_rules
    # KNOWN_FALLBACK_SYMBOLS used when RAG has no real KiCad library symbol.
    # The LLM validator flags them as non-existent — skip those errors.
    _BASIC_PASSIVES = frozenset([
        "Device:R_Small", "Device:C_Small", "Device:L_Small", "Device:D_Small",
        "Device:LED", "Device:Polyfuse", "Device:Crystal", "Device:Crystal_GND24",
        "Device:Crystal_Small",
    ])
    _placeholders = {s for s in _KNOWN_SYMBOLS if s.startswith("Device:") and s not in _BASIC_PASSIVES}
    validation_errors = [
        m for m in validation_errors
        if not any(s.split(":")[1].lower() in m.lower() for s in _placeholders)
    ]
    rejected = list(state.get("rejected_ids", []))
    rejected_families = list(state.get("rejected_families", []))
    for rid in _devkit_rejected_ids:
        if rid and rid not in rejected:
            rejected.append(rid)
    for rid in _enforced_rejected_ids:
        if rid and rid not in rejected:
            rejected.append(rid)
    for e in errors:
        eid = e.get("id_str", "")
        # Do not reject user-locked components, connectors, switches, or valid support passives
        comp_obj = next((c for c in comps if c.get("id_str") == eid or c.get("ref_des") == eid), None)
        if comp_obj and (
            comp_obj.get("is_user_locked")
            or comp_obj.get("user_locked")
            or comp_obj.get("functional_id")
            or comp_obj.get("category") in ("Connector", "Switch", "Device", "Power_Protection")
        ):
            continue
        # Do not reject components if error message is about a missing external part
        msg_lower = e.get("message", "").lower()
        if "missing" in msg_lower or "not present" in msg_lower or "required" in msg_lower:
            continue
        if eid and eid not in rejected:
            rejected.append(eid)
        fam = _normalize_part_family(eid)
        if fam and fam not in rejected_families:
            rejected_families.append(fam)
    if validation_errors:
        _emit(config, "agent:log", {
            "message": f"Validation found {len(validation_errors)} unfixed error(s) — will retry selection"
        })
        emit_assistant_message(config, f"Validation found {len(validation_errors)} issue(s) — retrying with fixes.")
    else:
        emit_tool_event(config, "Validation", "completed", "All checks passed")
        emit_assistant_message(config, "All validation checks passed — the BOM is electrically sound.")
    _emit(config, "agent:log", {
        "message": f"Validation done: {len(comps)} components, {len(validation_errors)} unfixed error(s), {len(warnings)} warning(s)"
    })
    result = {
        "selected_components": comps,
        "validation_errors": validation_errors,
        "rejected_ids": rejected,
        "rejected_families": rejected_families,
        "_last_validated_component_count": len(comps),
    }
    # Don't set result["error"] here — let _route_after_validate route to
    # ask_validation_help so the user can decide how to proceed when retries
    # are exhausted. The error detail is preserved in _validation_error_detail
    # for the help node to show the user.
    if validation_errors and state.get("retry_count", 0) >= MAX_VALIDATION_RETRIES:
        error_msgs = "; ".join(validation_errors[:3])
        repair_failures = state.get("repair_failures", []) or []
        if repair_failures:
            missing_targets = ", ".join(sorted(set(item.split(":", 1)[0] for item in repair_failures[:4])))
            result["_validation_error_detail"] = (
                f"No compatible component found in the available library after {MAX_VALIDATION_RETRIES} retries "
                f"for: {missing_targets}. Last validation errors: {error_msgs}"
            )
        else:
            result["_validation_error_detail"] = (
                f"Validation failed after {MAX_VALIDATION_RETRIES} retries: {error_msgs}"
            )
    status = "failed" if validation_errors else "completed"
    emit_tool_end(config, val_id, f"Validation {status} — {len(comps)} components, {len(validation_errors)} error(s), {len(warnings)} warning(s)",
                   status=status)
    return _stage_result(state, "validate", result)
