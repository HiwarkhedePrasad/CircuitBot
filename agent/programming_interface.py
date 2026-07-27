"""Deterministic programming-interface policy used before netlist generation."""

from __future__ import annotations


_NATIVE_USB_MCU_MARKERS = frozenset({
    "ESP32-C3", "ESP32-S2", "ESP32-S3", "ESP32-C6", "ESP32-H2",
    "RP2040", "RP2350", "SAMD11", "SAMD21", "SAMD51", "NRF52840",
    "ATMEGA32U4", "ATMEGA16U4", "AT90USB",
})

_USB_DATA_CONNECTOR_MARKERS = frozenset({
    "USB_C_RECEPTACLE_USB2", "MICRO_USB", "MINI_USB", "USB_B",
})

_GENERIC_PROGRAMMING_HEADERS = frozenset({
    "CONN_01X04", "CONN_01X06", "CONN_01X08", "CONN_02X03", "AVR-ISP",
})

_USB_UART_MARKERS = frozenset({"CP210", "CH340", "FT230", "FT232", "FTDI"})


def _id(component: dict) -> str:
    return (component.get("id_str", "") or "").upper()


def has_native_usb_mcu(components: list[dict]) -> bool:
    """Return whether the selected MCU can be programmed via native USB."""
    return any(any(marker in _id(component) for marker in _NATIVE_USB_MCU_MARKERS)
               for component in components if not component.get("builtin"))


def has_usb_data_connector(components: list[dict]) -> bool:
    """Return whether the BOM contains a USB connector with D+/D- capability."""
    return any(
        "POWERONLY" not in _id(component)
        and any(marker in _id(component) for marker in _USB_DATA_CONNECTOR_MARKERS)
        for component in components
    )


def has_programming_interface(components: list[dict]) -> bool:
    """Validate a real programming path, not merely the presence of a connector.

    Native-USB MCUs require a USB data connector. Other MCUs require a known
    UART/ISP header, or a USB-UART bridge connected to a USB data connector.
    ARM SWD headers are accepted only for STM32 designs.
    """
    if has_native_usb_mcu(components) and has_usb_data_connector(components):
        return True

    mcu_ids = [_id(component) for component in components if not component.get("builtin")]
    has_stm32 = any("STM32" in component_id for component_id in mcu_ids)

    for component in components:
        component_id = _id(component)
        if any(marker in component_id for marker in _GENERIC_PROGRAMMING_HEADERS):
            return True
        if has_stm32 and ("CORTEX_SWD" in component_id or "STDC14" in component_id):
            return True

    has_usb_uart_bridge = any(
        any(marker in _id(component) for marker in _USB_UART_MARKERS)
        for component in components
    )
    return has_usb_uart_bridge and has_usb_data_connector(components)
