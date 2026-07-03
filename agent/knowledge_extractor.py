"""Deterministic pin-role classification + structured component knowledge.

Extracts structured knowledge from component metadata and pin names
without requiring full datasheet downloads:

  - PinRole classification (UART_TX, ADC_IN, I2C_SDA, POWER_IN, etc.)
  - Interface detection (UART, I2C, SPI, ADC, USB, …)
  - Power rail identification
  - Programming / boot pin detection

At build time the full pipeline also pulls datasheet text to enrich
the structured knowledge (datasheet_summary, interface_pins, …).

Usage::

    from agent.knowledge_extractor import extract_knowledge

    knowledge = extract_knowledge(comp, pin_matrix)
    #  {
    #      "id_str": "MCU_Module:ESP32-WROOM-32D",
    #      "interfaces": ["UART", "I2C", "SPI", "ADC"],
    #      "pin_roles": { "1": "GROUND", "2": "POWER_IN", … },
    #      "power_rails": ["3.3V"],
    #      "programming_pins": {"ENABLE": "EN", "BOOT": "GPIO0"},
    #  }
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

# ── Pin-role classification ────────────────────────────────────────────────
# Order matters: more specific patterns first.

_ROLE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Power / Ground
    (re.compile(r'^(VDD|VCC|VIN|3V3|3\.3V|VDD[0-9]*|V\+|VPOS)$', re.IGNORECASE), 'POWER_IN'),
    (re.compile(r'^(5V|VUSB|VBUS)$', re.IGNORECASE), 'POWER_IN'),
    (re.compile(r'^(VOUT|VOUT_[0-9]+|3V3_OUT|1V8_OUT)$', re.IGNORECASE), 'POWER_OUT'),
    (re.compile(r'^(GND|GROUND|VSS|VEE|PGND|AGND|DGND|V\-|VNEG)$', re.IGNORECASE), 'GROUND'),
    (re.compile(r'^(VBAT|BAT|BATTERY)$', re.IGNORECASE), 'BATTERY'),
    # USB
    (re.compile(r'^(USB_DP|DP|D\+|D_P|USBD_P|USB_D\+)$', re.IGNORECASE), 'USB_DP'),
    (re.compile(r'^(USB_DN|DN|D\-|D_N|USBD_N|USB_D\-)$', re.IGNORECASE), 'USB_DN'),
    (re.compile(r'^(ID|USB_ID)$', re.IGNORECASE), 'USB_ID'),
    # UART
    (re.compile(r'^(TXD[0-9]*|TXD|TX|UART_TX|U0TXD|TXD0|TX0|SIMO|UART_TXD)$', re.IGNORECASE), 'UART_TX'),
    (re.compile(r'^(RXD[0-9]*|RXD|RX|UART_RX|U0RXD|RXD0|RX0|SOMI|UART_RXD)$', re.IGNORECASE), 'UART_RX'),
    (re.compile(r'^(CTS|RTS|UART_CTS|UART_RTS)$', re.IGNORECASE), 'UART_FLOW'),
    # I2C
    (re.compile(r'^(SDA|I2C_SDA|I2C[0-9]*_SDA)$', re.IGNORECASE), 'I2C_SDA'),
    (re.compile(r'^(SCL|I2C_SCL|I2C[0-9]*_SCL)$', re.IGNORECASE), 'I2C_SCL'),
    # SPI
    (re.compile(r'^(MOSI|SPI_MOSI|SPI[0-9]*_MOSI|MOST)$', re.IGNORECASE), 'SPI_MOSI'),
    (re.compile(r'^(MISO|SPI_MISO|SPI[0-9]*_MISO|MIST)$', re.IGNORECASE), 'SPI_MISO'),
    (re.compile(r'^(SCLK|SCK|SPI_SCK|SPI[0-9]*_SCK|CLK)$', re.IGNORECASE), 'SPI_SCK'),
    (re.compile(r'^(CS[0-9]*|SS|SSEL|SPI_CS|SPI[0-9]*_CS|#SS|~CS)$', re.IGNORECASE), 'SPI_CS'),
    # ADC / DAC
    (re.compile(r'^(ADC[0-9]*|ADC_IN[0-9]*|AIN[0-9]*)$', re.IGNORECASE), 'ADC_IN'),
    (re.compile(r'^(SENSOR_VP|SENSOR_VN|SENSOR_[A-Z]+)$', re.IGNORECASE), 'ADC_IN'),
    (re.compile(r'^(DAC[0-9]*|DAC_OUT[0-9]*|AOUT)$', re.IGNORECASE), 'DAC_OUT'),
    # JTAG / SWD
    (re.compile(r'^(TCK|JTAG_TCK)$', re.IGNORECASE), 'JTAG_TCK'),
    (re.compile(r'^(TMS|JTAG_TMS)$', re.IGNORECASE), 'JTAG_TMS'),
    (re.compile(r'^(TDI|JTAG_TDI)$', re.IGNORECASE), 'JTAG_TDI'),
    (re.compile(r'^(TDO|JTAG_TDO)$', re.IGNORECASE), 'JTAG_TDO'),
    (re.compile(r'^(SWDIO|SWIO|SWIM)$', re.IGNORECASE), 'SWD_IO'),
    (re.compile(r'^(SWCLK|SWCK|SWIM_CLK)$', re.IGNORECASE), 'SWD_CLK'),
    # System
    (re.compile(r'^(EN|EN_|ENABLE|CHIP_EN|CE)$', re.IGNORECASE), 'ENABLE'),
    (re.compile(r'^(RESET|RST|#RESET|~RESET|RESETB|RESET_N)$', re.IGNORECASE), 'RESET'),
    (re.compile(r'^(BOOT|BOOT[0-9]*|GPIO0)$', re.IGNORECASE), 'BOOT'),
    # Oscillator
    (re.compile(r'^(XTAL_IN|XTAL[0-9]*_IN|XI|OSC_IN)$', re.IGNORECASE), 'OSC_IN'),
    (re.compile(r'^(XTAL_OUT|XTAL[0-9]*_OUT|XO|OSC_OUT)$', re.IGNORECASE), 'OSC_OUT'),
    # IO expander / GPIO
    (re.compile(r'^(GPIO[0-9]*|IO[0-9]*|P[0-9][0-9]?)$', re.IGNORECASE), 'GPIO'),
    # Analog
    (re.compile(r'^(NC|NO_CONNECT)$', re.IGNORECASE), 'NC'),
    (re.compile(r'^(TEMP|VTEMP|TEMPOUT)$', re.IGNORECASE), 'ANALOG_OUT'),
    (re.compile(r'^(REF|VREF|REFIN|REFOUT|VREFH|VREFL)$', re.IGNORECASE), 'ANALOG_REF'),
    (re.compile(r'^(FB|FEEDBACK)$', re.IGNORECASE), 'FB'),
    (re.compile(r'^(COMP|COMP_OUT|COMP_[0-9]+)$', re.IGNORECASE), 'COMP'),
    # Switch / Button
    (re.compile(r'^(SW|SWITCH|BUTTON|KEY)$', re.IGNORECASE), 'SWITCH'),
    # LED
    (re.compile(r'^(LED|LED_[A-Z0-9]+|K|A|CATHODE|ANODE)$', re.IGNORECASE), 'LED'),
]

# Fallback by etype
_ETYPE_TO_ROLE: dict[str, str] = {
    "power_in": "POWER_IN",
    "power_out": "POWER_OUT",
    "input": "DIGITAL_IN",
    "output": "DIGITAL_OUT",
    "bidirectional": "BIDIRECTIONAL",
    "open_collector": "OPEN_DRAIN",
    "passive": "PASSIVE",
    "tri_state": "TRISTATE",
}

# ── Interface definitions (sets of required PinRoles) ──────────────────────

_INTERFACE_DEFS: list[tuple[str, set[str], int]] = [
    ("UART",     {"UART_TX", "UART_RX"}, 2),
    ("UART_FLOW", {"UART_TX", "UART_RX", "UART_FLOW"}, 2),
    ("I2C",      {"I2C_SDA", "I2C_SCL"}, 2),
    ("SPI",      {"SPI_MOSI", "SPI_MISO", "SPI_SCK"}, 3),
    ("SPI_CS",   {"SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS"}, 3),
    ("ADC",      {"ADC_IN"}, 1),
    ("DAC",      {"DAC_OUT"}, 1),
    ("USB",      {"USB_DP", "USB_DN"}, 2),
    ("JTAG",     {"JTAG_TCK", "JTAG_TMS", "JTAG_TDI", "JTAG_TDO"}, 4),
    ("SWD",      {"SWD_IO", "SWD_CLK"}, 2),
    ("OSCILLATOR", {"OSC_IN", "OSC_OUT"}, 2),
]

# ── Power rail detection ───────────────────────────────────────────────────

_POWER_RAIL_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^(VDD|VCC|VDD[0-9]*|3V3|3\.3V)$', re.IGNORECASE), '3.3V'),
    (re.compile(r'^(5V|VUSB|VBUS|VIN[0-9]*)$', re.IGNORECASE), '5V'),
    (re.compile(r'^(1V8|1\.8V|VDD[0-9]*_1V8)$', re.IGNORECASE), '1.8V'),
    (re.compile(r'^(VBAT|BAT)$', re.IGNORECASE), 'BATTERY'),
    (re.compile(r'^(VIN|VIN_[0-9]+|VCC_IN)$', re.IGNORECASE), 'VIN'),
    (re.compile(r'^(VOUT|VOUT_[0-9]+)$', re.IGNORECASE), 'VOUT'),
]

# ── Programming / boot pins ────────────────────────────────────────────────

_PROGRAMMING_PIN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r'^(EN|EN_|ENABLE|CHIP_EN|CE)$', re.IGNORECASE), 'ENABLE'),
    (re.compile(r'^(BOOT|BOOT[0-9]*|GPIO0)$', re.IGNORECASE), 'BOOT'),
    (re.compile(r'^(TXD[0-9]*|TXD|TX|UART_TX|U0TXD|TXD0)$', re.IGNORECASE), 'PROG_TX'),
    (re.compile(r'^(RXD[0-9]*|RXD|RX|UART_RX|U0RXD|RXD0)$', re.IGNORECASE), 'PROG_RX'),
    (re.compile(r'^(IO0|GPIO0)$', re.IGNORECASE), 'BOOT'),
    (re.compile(r'^(RESET|RST|~RST|#RST)$', re.IGNORECASE), 'RESET'),
]

# ── Helpers ────────────────────────────────────────────────────────────────


def _canonical(name: str) -> str:
    """Normalize KiCad pin name for matching — uppercase, strip formatting noise.
    
    Only strips KiCad markup ({, }, ~, ^) — preserves semantic underscores
    so that ``I2C_SDA``, ``UART_TX`` etc. keep their separators.
    Compare with ``pin_matcher._canonical`` which also strips underscores
    (matching against underscore-free lookup sets).
    """
    if not name:
        return ""
    return (
        name.strip()
        .upper()
        .replace("{", "")
        .replace("}", "")
        .replace("~", "")
        .replace("^", "")
        .replace(" ", "")
    )


def _classify_pin_role(pin_name: str, etype: str = "") -> str:
    """Classify a single pin name into a canonical PinRole.

    Uses name-based pattern matching first, with etype-based disambiguation
    for ambiguous names (e.g. VOUT = POWER_OUT on regulators vs ANALOG_OUT
    on sensors).
    """
    name = _canonical(pin_name)
    if not name:
        return _ETYPE_TO_ROLE.get(etype, "UNKNOWN")

    # Etype-based disambiguation for pin names that appear in both
    # power-delivery and signal contexts
    if "VOUT" in name.replace("_", "").replace("-", ""):
        if etype == "power_out":
            return "POWER_OUT"
        elif etype == "output":
            return "ANALOG_OUT"

    for pattern, role in _ROLE_PATTERNS:
        if pattern.match(name):
            return role

    return _ETYPE_TO_ROLE.get(etype, "UNKNOWN")


def _detect_interfaces(pin_roles: dict[str, str]) -> list[str]:
    """Detect supported interfaces from classified pin roles."""
    role_set = set(pin_roles.values())
    detected: list[str] = []
    for name, required, min_count in _INTERFACE_DEFS:
        if required.issubset(role_set):
            detected.append(name)
    return sorted(detected)


def _extract_power_rails(pin_roles: dict[str, str], pin_matrix: dict) -> list[str]:
    """Extract power rails from pin names and roles."""
    rails: list[str] = []
    seen: set[str] = set()
    for key, role in pin_roles.items():
        if role == "POWER_IN":
            pin_info = pin_matrix.get(key, {})
            name = _canonical(pin_info.get("name", ""))
            for pattern, rail in _POWER_RAIL_PATTERNS:
                if pattern.match(name) and rail not in seen:
                    rails.append(rail)
                    seen.add(rail)
                    break
            if not name and "3.3V" not in seen:
                rails.append("3.3V")
                seen.add("3.3V")
    return rails


def _extract_programming_pins(pin_roles: dict[str, str], pin_matrix: dict) -> dict[str, str]:
    """Extract programming/boot pin mapping."""
    prog_pins: dict[str, str] = {}
    for key, role in pin_roles.items():
        if role in ("ENABLE", "BOOT", "RESET"):
            prog_pins[role] = key.split(":")[-1]
        elif role in ("PROG_TX", "PROG_RX"):
            prog_pins[role] = key.split(":")[-1]
    return prog_pins


def _extract_interface_pin_map(pin_roles: dict[str, str], pin_matrix: dict) -> dict[str, dict[str, str]]:
    """Build a map of interface → {role: pin_num}."""
    # Reverse lookup: PinRole → list of pin keys
    role_to_pins: dict[str, list[str]] = {}
    for key, role in pin_roles.items():
        role_to_pins.setdefault(role, []).append(key)

    iface_map: dict[str, dict[str, str]] = {}

    # UART
    tx = role_to_pins.get("UART_TX", [])
    rx = role_to_pins.get("UART_RX", [])
    if tx and rx:
        uart: dict[str, str] = {}
        num = role_to_pins.get("UART_FLOW", [])
        uart["TX"] = tx[0].split(":")[-1]
        uart["RX"] = rx[0].split(":")[-1]
        if num:
            uart["CTS"] = num[0].split(":")[-1]
        iface_map["UART"] = uart

    # I2C
    sda = role_to_pins.get("I2C_SDA", [])
    scl = role_to_pins.get("I2C_SCL", [])
    if sda and scl:
        iface_map["I2C"] = {"SDA": sda[0].split(":")[-1], "SCL": scl[0].split(":")[-1]}

    # SPI
    mosi = role_to_pins.get("SPI_MOSI", [])
    miso = role_to_pins.get("SPI_MISO", [])
    sck = role_to_pins.get("SPI_SCK", [])
    if mosi and miso and sck:
        spi: dict[str, str] = {"MOSI": mosi[0].split(":")[-1], "MISO": miso[0].split(":")[-1], "SCK": sck[0].split(":")[-1]}
        cs = role_to_pins.get("SPI_CS", [])
        if cs:
            spi["CS"] = cs[0].split(":")[-1]
        iface_map["SPI"] = spi

    # ADC
    adc = role_to_pins.get("ADC_IN", [])
    if adc:
        iface_map["ADC"] = {f"CH{i}": p.split(":")[-1] for i, p in enumerate(adc)}

    # DAC
    dac = role_to_pins.get("DAC_OUT", [])
    if dac:
        iface_map["DAC"] = {f"CH{i}": p.split(":")[-1] for i, p in enumerate(dac)}

    # USB
    dp = role_to_pins.get("USB_DP", [])
    dn = role_to_pins.get("USB_DN", [])
    if dp and dn:
        iface_map["USB"] = {"DP": dp[0].split(":")[-1], "DN": dn[0].split(":")[-1]}

    return iface_map


# ── Public API ─────────────────────────────────────────────────────────────


def extract_knowledge(
    comp: dict,
    pin_matrix: dict[str, dict],
    datasheet_text: str = "",
    existing_roles: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Extract structured knowledge from a component and its pin matrix.

    Parameters
    ----------
    comp : dict
        Component dict with at least ``ref_des`` and ``id_str``.
    pin_matrix : dict[str, dict]
        Full pin matrix (``{key: {name, etype, ...}}``).
    datasheet_text : str
        Optional datasheet snippet for enrichment.
    existing_roles : dict[str, str] | None
        Previously classified pin roles (``{pin_key: role}``).
        If provided, only unclassified pins are re-classified.

    Returns
    -------
    dict with keys:
        id_str, interfaces, pin_roles, power_rails, programming_pins,
        interface_pins, datasheet_summary, analog_inputs
    """
    ref = comp["ref_des"]
    id_str = comp.get("id_str", "")

    pin_roles: dict[str, str] = dict(existing_roles or {})

    for key, pin in pin_matrix.items():
        if key.split(":")[0] != ref:
            continue
        if key in pin_roles:
            continue
        pin_name = pin.get("name", "")
        etype = pin.get("etype", "")
        pin_roles[key] = _classify_pin_role(pin_name, etype)

    interfaces = _detect_interfaces(pin_roles)
    power_rails = _extract_power_rails(pin_roles, pin_matrix)
    programming_pins = _extract_programming_pins(pin_roles, pin_matrix)
    interface_pins = _extract_interface_pin_map(pin_roles, pin_matrix)

    analog_inputs = [
        key.split(":")[-1] for key, role in pin_roles.items()
        if role in ("ADC_IN", "ANALOG_REF")
    ]

    datasheet_summary = ""
    if datasheet_text:
        ds_lower = datasheet_text.lower()
        voltage_specs: list[str] = []
        # Extract voltage references (with context keyword)
        for m in re.finditer(r'(\d[.]?\d*\s*V)\s*(?:supply|operating|input|output)', ds_lower, re.IGNORECASE):
            v = m.group(1)
            if v not in voltage_specs:
                voltage_specs.append(v)
        # Also capture standalone voltage mentions
        for m in re.finditer(r'(?:supply|operating|input|output)\s+voltage[:\s]+(\d[.]?\d*\s*V)', ds_lower, re.IGNORECASE):
            v = m.group(1)
            if v not in voltage_specs:
                voltage_specs.append(v)
        iface_lines: list[str] = []
        for iface in interfaces:
            pin_map = interface_pins.get(iface, {})
            if pin_map:
                parts = [f"{k}={v}" for k, v in pin_map.items()]
                iface_lines.append(f"{iface}({','.join(parts)})")
            else:
                iface_lines.append(iface)
        datasheet_summary_parts: list[str] = []
        if voltage_specs:
            datasheet_summary_parts.append(f"voltage={','.join(voltage_specs)}")
        if iface_lines:
            datasheet_summary_parts.append(f"iface={'|'.join(iface_lines)}")
        if datasheet_summary_parts:
            datasheet_summary = "; ".join(datasheet_summary_parts)

    return {
        "id_str": id_str,
        "interfaces": interfaces,
        "pin_roles": {
            k.split(":")[-1]: v for k, v in pin_roles.items()
        },
        "power_rails": power_rails,
        "programming_pins": programming_pins,
        "interface_pins": interface_pins,
        "analog_inputs": analog_inputs,
        "datasheet_summary": datasheet_summary,
    }


def format_knowledge_for_prompt(knowledge: dict[str, Any]) -> str:
    """Format structured knowledge into a compact LLM-friendly string.

    Returns a single line like::

        [SPI(MOSI=23,MISO=19,SCK=18)|I2C(SDA=21,SCL=22)|ADC(CH0=36)] power=3.3V prog=EN,GPIO0
    """
    parts: list[str] = []

    ifaces = knowledge.get("interface_pins", {})
    if ifaces:
        iface_strs: list[str] = []
        for name, pins in sorted(ifaces.items()):
            inner = ",".join(f"{k}={v}" for k, v in sorted(pins.items()))
            iface_strs.append(f"{name}({inner})")
        if iface_strs:
            parts.append(f"[{'|'.join(iface_strs)}]")

    rails = knowledge.get("power_rails", [])
    if rails:
        parts.append(f"power={','.join(rails)}")

    prog = knowledge.get("programming_pins", {})
    if prog:
        prog_str = ",".join(f"{k}={v}" for k, v in sorted(prog.items()))
        parts.append(f"prog={prog_str}")

    ds = knowledge.get("datasheet_summary", "")
    if ds:
        parts.append(f"ds={ds}")

    return " ".join(parts)


# ── Knowledge Database Builder ─────────────────────────────────────────────


_KNOWLEDGE_DB_PATH = os.path.join(
    os.path.dirname(__file__), "..", "netlist-preprocessing-experiment",
    "knowledge_db.json"
)


def load_knowledge_db(path: str = "") -> dict[str, dict]:
    """Load the persisted knowledge database from disk."""
    p = path or _KNOWLEDGE_DB_PATH
    if not os.path.isfile(p):
        return {}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_knowledge_db(db: dict[str, dict], path: str = ""):
    """Save the knowledge database to disk."""
    p = path or _KNOWLEDGE_DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def extract_knowledge_for_db(
    id_str: str,
    pins: list[dict],
    description: str,
    datasheet_text: str = "",
) -> dict[str, Any]:
    """Extract structured knowledge from raw RAG data (for DB building).

    Unlike ``extract_knowledge()`` this takes a pin list (not a pin matrix)
    and has no ref_des context.
    """
    # Build a synthetic pin matrix for classification
    pin_roles: dict[str, str] = {}
    interface_pins: dict[str, dict[str, str]] = {}

    for pin in pins:
        pname = pin.get("name", "")
        pnum = pin.get("num", "")
        etype = pin.get("type", "")
        role = _classify_pin_role(pname, etype)
        pin_roles[pnum] = role

    interfaces = _detect_interfaces(pin_roles)
    power_rails: list[str] = []
    seen_rails: set[str] = set()
    for pnum, role in pin_roles.items():
        if role == "POWER_IN":
            pin_info = next((p for p in pins if p.get("num", "") == pnum), {})
            pname = pin_info.get("name", "")
            for pattern, rail in _POWER_RAIL_PATTERNS:
                if pattern.match(pname) and rail not in seen_rails:
                    power_rails.append(rail)
                    seen_rails.add(rail)
                    break
    if not power_rails and any(r == "POWER_IN" for r in pin_roles.values()):
        power_rails.append("3.3V")

    programming_pins: dict[str, str] = {}
    for pnum, role in pin_roles.items():
        if role in ("ENABLE", "BOOT", "RESET", "PROG_TX", "PROG_RX"):
            programming_pins[role] = pnum

    analog_inputs = [p for p, r in pin_roles.items() if r in ("ADC_IN", "ANALOG_REF")]

    datasheet_summary = ""
    if datasheet_text:
        ds_lower = datasheet_text.lower()
        v_set: list[str] = []
        for m in re.finditer(r'(\d[.]?\d*\s*V)\s*(?:supply|operating|input|output)', ds_lower, re.IGNORECASE):
            v = m.group(1)
            if v not in v_set:
                v_set.append(v)
        for m in re.finditer(r'(?:supply|operating|input|output)\s+voltage[:\s]+(\d[.]?\d*\s*V)', ds_lower, re.IGNORECASE):
            v = m.group(1)
            if v not in v_set:
                v_set.append(v)
        voltage_specs = v_set
        ds_parts = []
        if voltage_specs:
            ds_parts.append(f"voltage={','.join(voltage_specs)}")
        if interfaces:
            ds_parts.append(f"iface={','.join(sorted(interfaces))}")
        if ds_parts:
            datasheet_summary = "; ".join(ds_parts)

    # Build cleaned interface pin map without ref context
    role_to_nums: dict[str, list[str]] = {}
    for pnum, role in pin_roles.items():
        role_to_nums.setdefault(role, []).append(pnum)

    iface_map: dict[str, dict[str, str]] = {}
    tx = role_to_nums.get("UART_TX", [])
    rx = role_to_nums.get("UART_RX", [])
    if tx and rx:
        iface_map["UART"] = {"TX": tx[0], "RX": rx[0]}

    sda = role_to_nums.get("I2C_SDA", [])
    scl = role_to_nums.get("I2C_SCL", [])
    if sda and scl:
        iface_map["I2C"] = {"SDA": sda[0], "SCL": scl[0]}

    mosi = role_to_nums.get("SPI_MOSI", [])
    miso = role_to_nums.get("SPI_MISO", [])
    sck = role_to_nums.get("SPI_SCK", [])
    if mosi and miso and sck:
        iface_map["SPI"] = {"MOSI": mosi[0], "MISO": miso[0], "SCK": sck[0]}

    dp = role_to_nums.get("USB_DP", [])
    dn = role_to_nums.get("USB_DN", [])
    if dp and dn:
        iface_map["USB"] = {"DP": dp[0], "DN": dn[0]}

    return {
        "id_str": id_str,
        "description": description[:200] if description else "",
        "interfaces": interfaces,
        "pin_roles": pin_roles,
        "power_rails": power_rails,
        "programming_pins": programming_pins,
        "interface_pins": iface_map,
        "analog_inputs": analog_inputs,
        "datasheet_summary": datasheet_summary,
    }
