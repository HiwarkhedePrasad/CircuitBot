"""
Direct-fetch component catalog — maps subsystem types to known-good parts.

Instead of searching + reranking (slow, error-prone), this module directly
returns the best component for common subsystems. Only falls back to
search/reranker when the part is unknown.

Usage:
    from agent.knowledge.component_catalog import resolve_component
    comp = resolve_component("regulator_3v3", "3.3V regulator for ESP32")
    # → {"id_str": "Regulator_Linear:AMS1117-3.3", "score": 10, ...}
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Known-good component mappings ──────────────────────────────────────
# Key: subsystem requirement name
# Value: dict with id_str, description, and optional RAG search fallback

KNOWN_COMPONENTS = {
    # ── Voltage Regulators ──────────────────────────────────────────────
    "regulator_3v3": {
        "id_str": "Regulator_Linear:AMS1117-3.3",
        "description": "3.3V 1A LDO regulator in SOT-223",
        "category": "Regulator_Linear",
        "alternatives": [
            "Regulator_Linear:AP2112K-3.3",
            "Regulator_Linear:ME6211C33",
        ],
    },
    "regulator_5v": {
        "id_str": "Regulator_Linear:LM7805",
        "description": "5V 1.5A linear regulator in TO-220",
        "category": "Regulator_Linear",
        "alternatives": [
            "Regulator_Linear:AMS1117-5.0",
            "Regulator_Linear:AP2112K-5.0",
        ],
    },
    "regulator_1v8": {
        "id_str": "Regulator_Linear:AMS1117-1.8",
        "description": "1.8V 1A LDO regulator",
        "category": "Regulator_Linear",
    },

    # ── USB Connectors ──────────────────────────────────────────────────
    "usb_connector": {
        "id_str": "Connector:USB_C_Receptacle_USB2.0_16P",
        "description": "USB Type-C receptacle, 16-pin, USB 2.0",
        "category": "Connector",
        "alternatives": [
            "Connector:USB_C_Receptacle_GCT_USB4105",
            "Connector:USB_B_Micro",
        ],
    },
    "usb_type_c": {
        "id_str": "Connector:USB_C_Receptacle_USB2.0_16P",
        "description": "USB Type-C receptacle, 16-pin",
        "category": "Connector",
    },
    "usb_micro": {
        "id_str": "Connector:USB_B_Micro",
        "description": "Micro USB Type-B receptacle",
        "category": "Connector",
    },

    # ── USB ESD Protection ──────────────────────────────────────────────
    "usb_esd": {
        "id_str": "Device:USBLC6-2SC6",
        "description": "USB ESD protection diode, 6-pin SOT-23-6",
        "category": "Power_Protection",
        "alternatives": [
            "Power_Protection:TPD6S300A",
            "Device:USBLC6-2SC6",
        ],
    },

    # ── Decoupling Capacitors ──────────────────────────────────────────
    "decoupling_caps": {
        "id_str": "Device:C_Small",
        "description": "100nF ceramic decoupling capacitor",
        "category": "Device",
        "count": 2,
        "value": "100nF",
    },
    "decoupling_100nf": {
        "id_str": "Device:C_Small",
        "description": "100nF ceramic decoupling capacitor",
        "category": "Device",
    },
    "bulk_cap": {
        "id_str": "Device:C_Small",
        "description": "10µF ceramic bulk capacitor",
        "category": "Device",
    },

    # ── Resistors ────────────────────────────────────────────────────────
    "enable_pullup": {
        "id_str": "Device:R_Small",
        "description": "10kΩ pull-up resistor for enable pin",
        "category": "Device",
        "value": "10k",
    },
    "boot_strapping": {
        "id_str": "Device:R_Small",
        "description": "10kΩ pull-up resistor for boot strapping",
        "category": "Device",
        "value": "10k",
    },
    "reset_pullup": {
        "id_str": "Device:R_Small",
        "description": "10kΩ pull-up resistor for reset pin",
        "category": "Device",
        "value": "10k",
    },
    "i2c_pullup": {
        "id_str": "Device:R_Small",
        "description": "4.7kΩ pull-up resistor for I2C bus",
        "category": "Device",
        "value": "4.7k",
    },
    "usb_cc_pulldown": {
        "id_str": "Device:R_Small",
        "description": "5.1kΩ CC pull-down resistor for USB-C",
        "category": "Device",
        "value": "5.1k",
    },

    # ── Crystals / Oscillators ──────────────────────────────────────────
    "crystal_40mhz": {
        "id_str": "Device:Crystal",
        "description": "40MHz crystal oscillator for ESP32",
        "category": "Device",
    },
    "crystal_16mhz": {
        "id_str": "Device:Crystal",
        "description": "16MHz crystal oscillator for ATmega",
        "category": "Device",
    },
    "crystal_12mhz": {
        "id_str": "Device:Crystal",
        "description": "12MHz crystal oscillator for USB",
        "category": "Device",
    },
    "crystal_8mhz": {
        "id_str": "Device:Crystal",
        "description": "8MHz crystal oscillator for STM32 HSE",
        "category": "Device",
    },

    # ── Debug / Programming ─────────────────────────────────────────────
    "swd_header": {
        "id_str": "Connector:Conn_ARM_Cortex_SWD_10",
        "description": "10-pin ARM Cortex SWD debug header",
        "category": "Connector",
    },
    "programming_header": {
        "id_str": "Connector:Conn_ARM_Cortex_SWD_10",
        "description": "Programming/debug header",
        "category": "Connector",
    },

    # ── LEDs ─────────────────────────────────────────────────────────────
    "status_led": {
        "id_str": "Device:LED",
        "description": "Status indicator LED, green",
        "category": "Device",
    },
    "power_led": {
        "id_str": "Device:LED",
        "description": "Power indicator LED, green",
        "category": "Device",
    },
    "error_led": {
        "id_str": "Device:LED",
        "description": "Error indicator LED, red",
        "category": "Device",
    },

    # ── LEDs with current limiting ──────────────────────────────────────
    "led_with_resistor": {
        "id_str": "Device:LED",
        "description": "LED with current-limiting resistor",
        "category": "Device",
        "support_parts": [
            {"search_query": "330 ohm resistor", "ref_des_prefix": "R", "description": "LED current-limiting resistor"},
        ],
    },

    # ── Sensors (common) ────────────────────────────────────────────────
    "temperature_sensor": {
        "id_str": "Sensor_Temperature:TMP117",
        "description": "TMP117 high-precision temperature sensor",
        "category": "Sensor_Temperature",
        "alternatives": [
            "Sensor_Temperature:DS18B20",
            "Sensor_Temperature:BME280",
        ],
    },
    "humidity_sensor": {
        "id_str": "Sensor_Temperature:BME280",
        "description": "BME280 temperature/humidity/pressure sensor",
        "category": "Sensor_Temperature",
    },

    # ── Connectors ──────────────────────────────────────────────────────
    "power_connector": {
        "id_str": "Connector:Barrel_Jack",
        "description": "DC barrel jack for power input",
        "category": "Connector",
    },
    "pin_header_2p54": {
        "id_str": "Connector:Conn_01x04",
        "description": "2.54mm pin header, 4-pin",
        "category": "Connector",
    },

    # ── Timer ICs (NE555) ──────────────────────────────────────────────
    "timer_ic": {
        "id_str": "Timer:NE555P",
        "description": "NE555 timer IC, through-hole DIP-8",
        "category": "Timer",
        "alternatives": [
            "Timer:NE555D",
            "Timer:NE555DR",
        ],
    },
    "ne555": {
        "id_str": "Timer:NE555P",
        "description": "NE555 timer IC, through-hole DIP-8",
        "category": "Timer",
    },
    "ne555_astable": {
        "id_str": "Timer:NE555P",
        "description": "NE555 timer IC for astable multivibrator circuit",
        "category": "Timer",
    },

    # ── Timer IC Support Components ─────────────────────────────────────
    "timing_resistor_ra": {
        "id_str": "Device:R_Small",
        "description": "Timing resistor RA (VCC to DISCH pin)",
        "category": "Device",
        "value": "10k",
    },
    "timing_resistor_rb": {
        "id_str": "Device:R_Small",
        "description": "Timing resistor RB (DISCH to THRESH pins)",
        "category": "Device",
        "value": "10k",
    },
    "timing_capacitor": {
        "id_str": "Device:C_Small",
        "description": "Timing capacitor Ct (THRESH/TRIG to GND)",
        "category": "Device",
        "value": "10uF",
    },
    "bypass_capacitor": {
        "id_str": "Device:C_Small",
        "description": "100nF bypass capacitor for VCC pin",
        "category": "Device",
        "value": "100nF",
    },
    "current_limit_resistor": {
        "id_str": "Device:R_Small",
        "description": "Current-limiting resistor for LED",
        "category": "Device",
        "value": "330",
    },
}


def resolve_component(requirement: str, description: str = "") -> Optional[dict]:
    """Directly resolve a known component requirement.

    Args:
        requirement: The requirement name (e.g. "regulator_3v3", "usb_esd")
        description: Optional description for logging

    Returns:
        Component dict with id_str, score, category, description, etc.
        Or None if the requirement is unknown.
    """
    # Normalize the requirement key
    key = requirement.lower().strip()

    # Direct lookup
    if key in KNOWN_COMPONENTS:
        entry = KNOWN_COMPONENTS[key]
        return {
            "id_str": entry["id_str"],
            "score": 10,  # Maximum score — known good part
            "category": entry.get("category", ""),
            "description": entry.get("description", description),
            "justification": f"Known-good component for {requirement}",
            "source": "catalog",
            "alternatives": entry.get("alternatives", []),
            "count": entry.get("count", 1),
            "value": entry.get("value", ""),
            "support_parts": entry.get("support_parts", []),
        }

    # Fuzzy match: try partial key matching
    for known_key, entry in KNOWN_COMPONENTS.items():
        if known_key in key or key in known_key:
            return {
                "id_str": entry["id_str"],
                "score": 9,
                "category": entry.get("category", ""),
                "description": entry.get("description", description),
                "justification": f"Fuzzy match for {requirement} → {known_key}",
                "source": "catalog_fuzzy",
                "alternatives": entry.get("alternatives", []),
                "count": entry.get("count", 1),
                "value": entry.get("value", ""),
                "support_parts": entry.get("support_parts", []),
            }

    return None


def get_known_parts() -> list[str]:
    """Get list of all known requirement names."""
    return list(KNOWN_COMPONENTS.keys())


def is_known_requirement(requirement: str) -> bool:
    """Check if a requirement has a known-good component."""
    key = requirement.lower().strip()
    if key in KNOWN_COMPONENTS:
        return True
    for known_key in KNOWN_COMPONENTS:
        if known_key in key or key in known_key:
            return True
    return False
