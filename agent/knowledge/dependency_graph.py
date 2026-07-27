"""MCU dependency graph — what each MCU family requires.

Used by the dependency expander to inject required support components
and by the architecture planner to understand what a devkit already provides.

Each MCU entry has:
  - requires: dict of required support capabilities
  - devkit_overrides: which requirements are satisfied by a devkit
"""

from typing import Any


DEPENDENCY_GRAPH: dict[str, dict[str, Any]] = {
    "ESP32-C3": {
        "requires": {
            "usb_connector": {"required": True, "compatible": ["Connector:USB_C_Receptacle_USB2.0_16P"], "note": "native USB serial/JTAG data connector"},
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_40mhz": {"required": True, "note": "internal RC oscillator, external optional"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "boot_strapping": {"required": True, "note": "GPIO9 pull-up for boot"},
            "enable_pullup": {"required": True, "note": "10k to EN pin"},
        },
        "owns": ["usb", "uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "usb_connector": True,
            "regulator_3v3": True,
            "boot_strapping": True,
            "enable_pullup": True,
        },
        "module_overrides": {
            "crystal_40mhz": True,
        },
    },
    "ESP32-S3": {
        "requires": {
            "usb_connector": {"required": True, "compatible": ["Connector:USB_C_Receptacle_USB2.0_16P"], "note": "native USB serial/JTAG data connector"},
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_40mhz": {"required": True, "note": "external crystal required"},
            "decoupling_caps": {"required": True, "count": 4, "value": "100nF"},
            "boot_strapping": {"required": True, "note": "GPIO0 pull-up for boot"},
            "enable_pullup": {"required": True, "note": "10k to EN pin"},
        },
        "owns": ["usb", "uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "usb_connector": True,
            "regulator_3v3": True,
            "boot_strapping": True,
            "enable_pullup": True,
        },
        "module_overrides": {
            "crystal_40mhz": True,
        },
    },
    "ESP32-C6": {
        "requires": {
            "usb_connector": {"required": True, "compatible": ["Connector:USB_C_Receptacle_USB2.0_16P"], "note": "native USB serial/JTAG data connector"},
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_40mhz": {"required": True, "note": "external crystal required"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "boot_strapping": {"required": True, "note": "GPIO9 pull-up for boot"},
            "enable_pullup": {"required": True, "note": "10k to EN pin"},
        },
        "owns": ["usb", "uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "usb_connector": True,
            "regulator_3v3": True,
            "boot_strapping": True,
            "enable_pullup": True,
        },
        "module_overrides": {
            "crystal_40mhz": True,
        },
    },
    "STM32": {
        "requires": {
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_8mhz": {"required": True, "note": "HSE crystal for main clock"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "boot_strapping": {"required": True, "note": "BOOT0 pin strapping"},
            "swd_header": {"required": False, "note": "SWD debug header"},
        },
        "owns": ["uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "regulator_3v3": True,
            "boot_strapping": True,
            "swd_header": True,
        },
        "module_overrides": {},
    },
    "RP2040": {
        "requires": {
            "usb_connector": {"required": True, "note": "native USB, no bridge needed"},
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_12mhz": {"required": True, "note": "12MHz crystal for USB clock"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "flash": {"required": True, "note": "QSPI flash for program storage"},
        },
        "owns": ["usb", "uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "regulator_3v3": True,
            "flash": True,
        },
        "module_overrides": {
            "crystal_12mhz": True,
        },
    },
    "RP2350": {
        "requires": {
            "usb_connector": {"required": True, "note": "native USB, no bridge needed"},
            "regulator_3v3": {"required": True, "compatible": ["Regulator_Linear:AMS1117-3.3", "Regulator_Linear:AP2112K-3.3", "Regulator_Linear:XC6206P302MR"]},
            "crystal_12mhz": {"required": True, "note": "12MHz crystal for USB clock"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "flash": {"required": True, "note": "QSPI flash for program storage"},
        },
        "owns": ["usb", "uart", "led", "boot_button", "reset_button"],
        "devkit_overrides": {
            "regulator_3v3": True,
            "flash": True,
        },
        "module_overrides": {
            "crystal_12mhz": True,
        },
    },
    "ATmega328": {
        "requires": {
            "crystal_16mhz": {"required": True, "note": "16MHz crystal for main clock"},
            "decoupling_caps": {"required": True, "count": 2, "value": "100nF"},
            "reset_pullup": {"required": True, "note": "10k pull-up to VCC"},
            "aref_cap": {"required": True, "note": "100nF cap on AREF pin"},
        },
        "owns": ["uart", "led", "reset_button"],
        "devkit_overrides": {
            "crystal_16mhz": True,
            "reset_pullup": True,
            "aref_cap": True,
        },
        "module_overrides": {},
    },
    "ATmega32U4": {
        "requires": {
            "crystal_16mhz": {"required": True, "note": "16MHz crystal for main clock"},
            "decoupling_caps": {"required": True, "count": 3, "value": "100nF"},
            "reset_pullup": {"required": True, "note": "10k pull-up to VCC"},
            "aref_cap": {"required": True, "note": "100nF cap on AREF pin"},
            "ucap_cap": {"required": True, "note": "1uF on UCAP pin for USB regulator"},
        },
        "owns": ["usb", "uart", "led", "reset_button"],
        "devkit_overrides": {
            "crystal_16mhz": True,
            "reset_pullup": True,
            "aref_cap": True,
        },
        "module_overrides": {},
    },
    "NE555": {
        "requires": {
            "timing_resistor_ra": {
                "required": True,
                "note": "10k resistor between VCC and DISCH pin",
                "compatible": ["Device:R", "Device:R_Small"],
                "preferred_id_str": "Device:R_Small",
                "value": "10k",
            },
            "timing_resistor_rb": {
                "required": True,
                "note": "10k resistor between DISCH and THRESH pins",
                "compatible": ["Device:R", "Device:R_Small"],
                "preferred_id_str": "Device:R_Small",
                "value": "10k",
            },
            "timing_capacitor": {
                "required": True,
                "note": "10uF cap between THRESH/TRIG and GND",
                "compatible": ["Device:C", "Device:C_Small"],
                "preferred_id_str": "Device:C_Small",
                "value": "10uF",
            },
            "bypass_capacitor": {
                "required": True,
                "note": "100nF cap on VCC pin",
                "compatible": ["Device:C", "Device:C_Small"],
                "preferred_id_str": "Device:C_Small",
                "value": "100nF",
            },
            "current_limit_resistor": {
                "required": True,
                "note": "330 ohm for LED output",
                "compatible": ["Device:R", "Device:R_Small"],
                "preferred_id_str": "Device:R_Small",
                "value": "330",
            },
        },
        "owns": [],
        "devkit_overrides": {},
        "module_overrides": {},
    },
}


def get_mcu_family(id_str: str) -> str | None:
    """Extract the MCU family from a component id_str.
    
    Examples:
        "MCU_Espressif:ESP32-C3" -> "ESP32-C3"
        "MCU_ST:STM32F103C8T6" -> "STM32"
        "MCU_Microchip AVR:ATmega328P" -> "ATmega328"
        "MCU_Raspberry_Pi:RP2040" -> "RP2040"
    """
    text = id_str.upper()
    
    # Check specific variants first
    for family in DEPENDENCY_GRAPH:
        if family.upper() in text:
            return family
    
    # Check generic families
    if "STM32" in text:
        return "STM32"
    if "ATMEGA32U4" in text:
        return "ATmega32U4"
    if "ATMEGA" in text:
        return "ATmega328"
    if "ATTINY" in text:
        return "ATmega328"  # similar requirements
    if "RP2350" in text:
        return "RP2350"
    if "RP2040" in text:
        return "RP2040"
    
    return None


def get_requirements(mcu_family: str, board_type: str = "bare_ic") -> dict[str, dict]:
    """Get requirements for an MCU family, applying board type overrides.
    
    Returns dict of requirement_id -> requirement_spec for components
    that still need to be added.
    """
    entry = DEPENDENCY_GRAPH.get(mcu_family, {})
    requires = dict(entry.get("requires", {}))
    
    # Apply overrides based on board type
    overrides = {}
    if board_type in ("devkit", "module"):
        overrides.update(entry.get("devkit_overrides", {}))
        overrides.update(entry.get("module_overrides", {}))
    else:
        overrides.update(entry.get(f"{board_type}_overrides", {}))
    
    for req_id, satisfied in overrides.items():
        if satisfied and req_id in requires:
            del requires[req_id]
    
    return requires


def get_owned_capabilities(mcu_family: str) -> list[str]:
    """Get list of capabilities owned by this MCU (e.g. usb, uart, led)."""
    entry = DEPENDENCY_GRAPH.get(mcu_family, {})
    return list(entry.get("owns", []))
