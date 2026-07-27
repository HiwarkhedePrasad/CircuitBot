"""Centralized deterministic library prefix registry.

This is the SINGLE source of truth for mapping between:
- Subsystem names → allowed KiCad library prefixes (for RAG filtering)
- Subsystem names → expected type buckets (for candidate validation)
- Library prefixes → component type categories
- Library prefixes → reference designator prefixes
- Known wrong prefixes → correct prefix fixes

All pipeline files import from here instead of maintaining
their own scattered mappings.  Changes to library assignments
only need to happen in one place.
"""

from typing import FrozenSet


# ── Subsystem → allowed KiCad library prefixes ──────────────────────────
# Used by: templates/matcher.py (replaces get_library_filter),
#           research.py (RAG search filter), select.py (candidate validation)

SUBSYSTEM_LIBRARY_FILTERS: dict[str, str] = {
    "microcontroller":  "MCU_Espressif|MCU_Module|MCU_ST|MCU_Microchip|MCU_RaspberryPi|MCU_Nordic|MCU_NXP|MCU_Texas|MCU_AnalogDevices|MCU_Cypress|MCU_Dialog|MCU_Intel|MCU_Parallax|MCU_Puya|MCU_Renesas|MCU_SiFive|MCU_SiliconLabs|MCU_STC|MCU_Trident|MCU_WCH_RiscV|RF_Module",
    "mcu":              "MCU_Espressif|MCU_Module|MCU_ST|MCU_Microchip|MCU_RaspberryPi|MCU_Nordic|MCU_NXP|MCU_Texas|MCU_AnalogDevices|MCU_Cypress|MCU_Dialog|MCU_Intel|MCU_Parallax|MCU_Puya|MCU_Renesas|MCU_SiFive|MCU_SiliconLabs|MCU_STC|MCU_Trident|MCU_WCH_RiscV|RF_Module",
    "processing":       "MCU_Espressif|MCU_Module|MCU_ST|MCU_Microchip|MCU_RaspberryPi|MCU_Nordic|MCU_NXP|MCU_Texas|MCU_AnalogDevices|MCU_Cypress|MCU_Dialog|MCU_Renesas|RF_Module",
    "power input":      "Connector|Connector_USB",
    "power regulation": "Regulator_Linear|Regulator_Switching|Regulator_Controller|Regulator_Current|Regulator_SwitchedCapacitor",
    "power":            "Regulator_Linear|Regulator_Switching|Regulator_Controller|Regulator_Current|Power_Management|Power_Protection|Power_Supervisor",
    "usb-uart":         "Interface_USB|Connector_USB",
    "bridge":           "Interface_USB|Connector_USB",
    "temperature":      "Sensor_Temperature|Sensor",
    "environmental":    "Sensor_Temperature|Sensor",
    "sensor":           "Sensor_Temperature|Sensor_Audio|Sensor_Current|Sensor_Distance|Sensor_Energy|Sensor_Gas|Sensor_Humidity|Sensor_Magnetic|Sensor_Motion|Sensor_Optical|Sensor_Pressure|Sensor_Proximity|Sensor_Touch|Sensor_Voltage",
    "display":          "Display_Character|Display_Graphic",
    "connector":        "Connector|Connector_Audio|Connector_Generic|Connector_Generic_Shielded|Connector_USB",
    "led":              "Device|LED",
    "status":           "Device|LED",
    "programming":      "Connector",
    "debug":            "Connector",
    "usb":              "Connector|Connector_USB",
    "battery":          "Battery_Management",
    "clock":            "Oscillator|Timer|Timer_RTC|Timer_PLL",
    "crystal":          "Oscillator|Device",
    "memory":           "Memory_EEPROM|Memory_Flash|Memory_RAM|Memory_NVRAM|Memory_EPROM|Memory_ROM|Memory_UniqueID",
    "esd":              "Power_Protection",
    "protection":       "Power_Protection|Diode",
    "wifi":             "RF_WiFi|RF_Module",
    "bluetooth":        "RF_Bluetooth|RF_Module",
    "wireless":         "RF_Module|RF|RF_WiFi|RF_Bluetooth|RF_GPS|RF_GSM|RF_NFC|RF_RFID|RF_ZigBee",
    "motor":            "Driver_Motor",
    "driver":           "Driver_Motor|Driver_LED|Driver_FET|Driver_Display|Driver",
    "reset":            "Switch|Device",
    "button":           "Switch",
    "switch":           "Switch",
    "transistor":       "Transistor_FET|Transistor_BJT|Transistor_Array|Transistor_IGBT|Transistor_Power_Module",
}


# ── Subsystem → expected type buckets ───────────────────────────────────
# Used by: select.py (replaces _SUBSYSTEM_EXPECTATION_HINTS)

SUBSYSTEM_TYPE_BUCKETS: dict[str, FrozenSet[str]] = {
    "power input":        frozenset({"connector"}),
    "power regulation":   frozenset({"regulator_switching", "regulator_linear"}),
    "power":              frozenset({"regulator_switching", "regulator_linear"}),
    "sensor":             frozenset({"sensor"}),
    "sensing":            frozenset({"sensor"}),
    "temperature":        frozenset({"sensor"}),
    "environmental":      frozenset({"sensor"}),
    "microcontroller":    frozenset({"mcu"}),
    "mcu":                frozenset({"mcu"}),
    "processing":         frozenset({"mcu"}),
    "wireless":           frozenset({"mcu", "driver"}),
    "status indicator":   frozenset({"led"}),
    "passive":            frozenset({"resistor", "capacitor", "inductor", "diode"}),
    "connector":          frozenset({"connector"}),
    "display":            frozenset({"display"}),
    "programming":        frozenset({"connector"}),
    "debug":              frozenset({"connector"}),
    "battery":            frozenset({"connector", "regulator_linear"}),
    "memory":             frozenset({"memory"}),
    "usb-uart":           frozenset({"usb_uart"}),
    "bridge":             frozenset({"usb_uart"}),
    "usb interface":       frozenset({"usb_uart"}),
    "usb":                frozenset({"usb_uart", "connector"}),
}


# ── Library prefix → reference designator prefix ────────────────────────
# Used by: select.py / _ref_prefix() for component numbering

LIBRARY_REF_DES_PREFIX: dict[str, str] = {
    "Connector": "J",
    "Connector_USB": "J",
    "Connector_Audio": "J",
    "Connector_Generic": "J",
    "Battery_Management": "U",
    "Display_Character": "U",
    "Display_Graphic": "U",
    "Interface_USB": "U",
    "Interface_UART": "U",
    "Interface": "U",
    "MCU_": "U",
    "Memory_EEPROM": "U",
    "Memory_Flash": "U",
    "Memory_RAM": "U",
    "Oscillator": "Y",
    "Power_Management": "U",
    "Power_Protection": "U",
    "Power_Supervisor": "U",
    "Regulator_Linear": "U",
    "Regulator_Switching": "U",
    "Regulator_Controller": "U",
    "RF_Module": "U",
    "RF": "U",
    "Sensor_Temperature": "U",
    "Sensor": "U",
    "Switch": "SW",
    "Timer": "U",
    "Transistor_FET": "Q",
    "Transistor_BJT": "Q",
    "Driver_Motor": "U",
    "Driver": "U",
    "Diode": "D",
    "LED": "D",
}

# Default for unrecognized libraries
_DEFAULT_REF_DES = "U"


# ── Library prefix → category string ──────────────────────────────────
# Used by: validate.py, netlist.py for component classification

LIBRARY_CATEGORY: dict[str, str] = {
    "Battery_Management": "BATTERY_MGMT",
    "Connector": "CONNECTOR",
    "Connector_Audio": "CONNECTOR",
    "Connector_Generic": "CONNECTOR",
    "Connector_USB": "CONNECTOR",
    "Display_Character": "DISPLAY",
    "Display_Graphic": "DISPLAY",
    "Interface": "INTERFACE",
    "Interface_UART": "INTERFACE",
    "Interface_USB": "INTERFACE",
    "MCU_": "MCU",
    "Memory_EEPROM": "MEMORY",
    "Memory_Flash": "MEMORY",
    "Oscillator": "CRYSTAL",
    "Power_Management": "POWER_MGMT",
    "Power_Protection": "PROTECTION",
    "Power_Supervisor": "POWER_MGMT",
    "Regulator_Linear": "REGULATOR",
    "Regulator_Switching": "REGULATOR",
    "Regulator_Controller": "REGULATOR",
    "RF": "RF",
    "RF_Module": "RF_MODULE",
    "RF_WiFi": "RF",
    "RF_Bluetooth": "RF",
    "Sensor": "SENSOR",
    "Sensor_Temperature": "SENSOR",
    "Switch": "SWITCH",
    "Timer": "TIMER",
    "Transistor_BJT": "TRANSISTOR",
    "Transistor_FET": "TRANSISTOR",
}

_DEFAULT_CATEGORY = "IC"


# ── Known wrong → correct library prefix fixes ────────────────────────
# Applied by validate.py before BOM validation so the rest of the
# pipeline sees the correct KiCad prefix.

LIBRARY_PREFIX_FIXES: dict[str, str] = {
    "Device:TPD6S300A":      "Power_Protection:TPD6S300A",
    "Device:USBLC6-2SC6":    "Power_Protection:USBLC6-2SC6",
    "Device:IP4234CZ10":     "Power_Protection:IP4234CZ10",
    "Connector_USB:TPD6S300A": "Power_Protection:TPD6S300A",
    "Connector_USB:USBLC6-2SC6": "Power_Protection:USBLC6-2SC6",
    "Connector_USB:USB_C_":  "Connector:USB_C_",
    "Connector_USB:USB_":    "Connector:USB_",
    "Connector_USB:USB2":    "Connector:USB2",
    "MCU_ESP32:":            "MCU_Espressif:",  # Fallback code uses wrong prefix
    "MCU_ESP8266:":          "MCU_Espressif:",  # Same chip family
}


# ── MCU library prefix patterns ───────────────────────────────────────
# Used by: select.py (arch filter), deduplicator.py (MCU dedup),
#           constraint_checker.py (MCU checks)

MCU_LIBRARY_PREFIXES: tuple[str, ...] = (
    "MCU_",              # Catch-all for any MCU_* library
    "RF_Module:",        # MCU modules (ESP32-WROOM, etc.)
    "CPU",               # CPU-class libraries
    "DSP_",              # DSP processors
)


# ── Lookup helpers ──────────────────────────────────────────────────────


def get_library_filter(subsystem_name: str) -> str:
    """Get the KiCad library filter string for a given subsystem name.

    Returns a pipe-separated filter string (e.g. ``"MCU_Espressif|MCU_ST"``)
    or empty string if no filter is known.
    """
    key = subsystem_name.strip().lower()
    if key in SUBSYSTEM_LIBRARY_FILTERS:
        return SUBSYSTEM_LIBRARY_FILTERS[key]
    for kw, filt in SUBSYSTEM_LIBRARY_FILTERS.items():
        if kw in key:
            return filt
    return ""


def get_type_buckets(subsystem_name: str) -> FrozenSet[str]:
    """Get the expected type buckets for a subsystem name."""
    key = subsystem_name.strip().lower()
    if key in SUBSYSTEM_TYPE_BUCKETS:
        return SUBSYSTEM_TYPE_BUCKETS[key]
    for kw, buckets in SUBSYSTEM_TYPE_BUCKETS.items():
        if kw in key:
            return buckets
    return frozenset()


def get_ref_des_prefix(library_prefix: str) -> str:
    """Get the reference designator prefix for a KiCad library prefix.

    ``library_prefix`` is the part before ``:`` in a component id_str
    (e.g. ``"MCU_Espressif"`` from ``MCU_Espressif:ESP32-C3``).
    """
    if library_prefix in LIBRARY_REF_DES_PREFIX:
        return LIBRARY_REF_DES_PREFIX[library_prefix]
    for pat, ref in LIBRARY_REF_DES_PREFIX.items():
        if pat.endswith("_") and library_prefix.startswith(pat):
            return ref
        if pat.endswith(":") and library_prefix.startswith(pat.rstrip(":")):
            return ref
        if pat in library_prefix:
            return ref
    return _DEFAULT_REF_DES


def get_category(library_prefix: str) -> str:
    """Get the component category for a KiCad library prefix."""
    if library_prefix in LIBRARY_CATEGORY:
        return LIBRARY_CATEGORY[library_prefix]
    for pat, cat in LIBRARY_CATEGORY.items():
        if pat.endswith("_") and library_prefix.startswith(pat):
            return cat
        if pat in library_prefix or library_prefix.startswith(pat):
            return cat
    return _DEFAULT_CATEGORY


def is_mcu_prefix(id_str_or_lib: str) -> bool:
    """Check whether an id_str or library prefix is an MCU component."""
    lib = id_str_or_lib.split(":")[0] if ":" in id_str_or_lib else id_str_or_lib
    upper = lib.upper()
    for prefix in MCU_LIBRARY_PREFIXES:
        p = prefix.rstrip("_:").upper()
        if upper == p or upper.startswith(p):
            return True
    return False


def fix_library_prefix(id_str: str) -> str:
    """Apply known prefix fixes to an id_str."""
    for wrong, right in LIBRARY_PREFIX_FIXES.items():
        if id_str.startswith(wrong):
            return right + id_str[len(wrong):]
    return id_str


def extract_library_prefix(id_str: str) -> str:
    """Extract the KiCad library prefix from a component id_str.

    ``"MCU_Espressif:ESP32-C3"`` → ``"MCU_Espressif"``
    """
    return id_str.split(":")[0] if ":" in id_str else id_str
