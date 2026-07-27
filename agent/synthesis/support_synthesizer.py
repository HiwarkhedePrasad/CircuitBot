"""Deterministic Hardware Synthesizer for CircuitBot.

Generates 100% discrete, unique support components (passives, push buttons,
protection diodes, LEDs, bulk/decoupling capacitors) for active ICs and
connectors in the design state.

Every generated support part has a unique functional_id (e.g. R_CC1, R_CC2,
SW_RESET, SW_BOOT, D_SCHOTTKY) so deduplication never merges distinct parts.
"""

from __future__ import annotations
import logging
from typing import Any
from agent.state_models import ComponentModel, make_functional_id

logger = logging.getLogger(__name__)


def synthesize_support_components(
    selected_components: list[dict[str, Any]],
    prompt: str = "",
) -> list[dict[str, Any]]:
    """Synthesize discrete support passives, switches, LEDs, and protection components."""
    prompt_upper = prompt.upper()
    existing_ids = {c.get("id_str", "") for c in selected_components}
    existing_func_ids = {c.get("functional_id", "") for c in selected_components}
    synthesized: list[dict[str, Any]] = []

    existing_descs = {c.get("description", "").upper() for c in selected_components}
    def _add_synth(func_id: str, id_str: str, category: str, desc: str, val: str, sub: str):
        if func_id in existing_func_ids:
            return
        desc_up = desc.upper()
        if any(desc_up in d or d in desc_up for d in existing_descs if len(d) > 5):
            return
        existing_func_ids.add(func_id)
        existing_descs.add(desc_up)
        comp = ComponentModel(
            functional_id=func_id,
            id_str=id_str,
            category=category,
            description=desc,
            value=val,
            is_user_locked=False,
            subsystem=sub,
            justification=f"Deterministically synthesized support part for {sub}",
        ).to_dict()
        synthesized.append(comp)

    # 1. USB-C Power Receptacle Support
    has_usbc = any(
        "USB" in c.get("id_str", "").upper() and ("TYPE-C" in c.get("id_str", "").upper() or "USB_C" in c.get("id_str", "").upper())
        for c in selected_components
    ) or any(kw in prompt_upper for kw in ("USB-C", "TYPE-C", "TYPE C", "USB_C"))
    if has_usbc:
        _add_synth("R_CC1", "Device:R_Small", "Device", "5.1kΩ USB-C CC1 pull-down resistor", "5.1k", "Power Input")
        _add_synth("R_CC2", "Device:R_Small", "Device", "5.1kΩ USB-C CC2 pull-down resistor", "5.1k", "Power Input")

    # 2. Reverse Polarity Protection Schottky Diode
    need_schottky = "SCHOTTKY" in prompt_upper or "REVERSE" in prompt_upper or "POLARITY" in prompt_upper or has_usbc
    if need_schottky:
        _add_synth("D_SCHOTTKY", "Device:D_Schottky", "Device", "Schottky diode for VBUS reverse-polarity protection", "MBR0520", "Power Input")

    # 3. 5V Rail Bulk Decoupling Capacitor
    if has_usbc or ("5V" in prompt_upper and "POWER" in prompt_upper):
        _add_synth("C_5V_BULK", "Device:C_Small", "Device", "10µF 5V rail bulk input capacitor", "10uF", "Power Input")

    # 4. Voltage Regulator (AMS1117-3.3 / LDO) Support
    has_regulator = any(
        "REGULATOR" in c.get("category", "").upper() or "AMS1117" in c.get("id_str", "").upper() or "LDO" in c.get("id_str", "").upper()
        for c in selected_components
    ) or "3.3V" in prompt_upper or "AMS1117" in prompt_upper
    if has_regulator:
        _add_synth("C_3V3_BULK", "Device:C_Small", "Device", "10µF 3.3V regulated rail bulk capacitor", "10uF", "Power Regulation")
        _add_synth("C_3V3_DEC", "Device:C_Small", "Device", "100nF 3.3V regulated rail decoupling capacitor", "100nF", "Power Regulation")

    # 5. ESP32 Module Support (EN/BOOT Pull-ups & Push Buttons)
    has_esp32 = any(
        "ESP32" in c.get("id_str", "").upper() or "RF_MODULE" in c.get("category", "").upper()
        for c in selected_components
    ) or "ESP32" in prompt_upper
    if has_esp32:
        _add_synth("R_EN_PULLUP", "Device:R_Small", "Device", "10kΩ EN pin pull-up resistor", "10k", "Microcontroller")
        _add_synth("R_BOOT_PULLUP", "Device:R_Small", "Device", "10kΩ BOOT (GPIO9) pin pull-up resistor", "10k", "Microcontroller")
        _add_synth("SW_RESET", "Switch:SW_Push", "Switch", "Tactile push button for RESET (EN to GND)", "6x6mm", "Microcontroller")
        _add_synth("SW_BOOT", "Switch:SW_Push", "Switch", "Tactile push button for BOOT (GPIO9 to GND)", "6x6mm", "Microcontroller")
        _add_synth("C_MCU_DEC1", "Device:C_Small", "Device", "100nF decoupling capacitor for ESP32 module", "100nF", "Microcontroller")

    # 6. LM35 Temperature Sensor Support
    has_lm35 = any(
        "LM35" in c.get("id_str", "").upper() or "LM35" in c.get("subsystem", "").upper()
        for c in selected_components
    ) or "LM35" in prompt_upper
    if has_lm35:
        _add_synth("C_LM35_BYPASS", "Device:C_Small", "Device", "100nF bypass capacitor for LM35 temperature sensor", "100nF", "Temperature Sensor")

    # 7. Power Status Indicator LED & Resistor
    need_led = "LED" in prompt_upper or "INDICATOR" in prompt_upper
    if need_led:
        _add_synth("D_POWER_LED", "Device:LED_Small", "Device", "Green power status indicator LED", "Green", "Power Input")
        _add_synth("R_LED_LIMIT", "Device:R_Small", "Device", "330Ω current-limiting resistor for power LED", "330", "Power Input")

    # 8. Programming & GPIO Expansion Headers
    need_header = any(kw in prompt_upper for kw in ("HEADER", "PROGRAMMING", "UART", "EXPANSION", "PIN"))
    has_header = any("CONNECTOR" in c.get("category", "").upper() and "CONN_01" in c.get("id_str", "").upper() for c in selected_components)
    if need_header and not has_header:
        _add_synth("J_PROG_HDR", "Connector:Conn_01x06_Pin", "Connector", "6-pin programming & UART header (TX, RX, 3V3, 5V, GND, ADC)", "1x06", "Programming & Expansion")

    return synthesized
