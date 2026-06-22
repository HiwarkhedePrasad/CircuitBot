import re
from collections import Counter


def generate_pin_summary(pins_list: list[dict]) -> str:
    """Convert a raw pin list into a token-efficient LLM summary.

    Example output:
        "Total Pins: 38 (3 power_in, 26 bidirectional, 4 input). Supports: I2C, UART, SPI"
    """
    if not pins_list:
        return "Pin data unavailable."

    total_pins = len(pins_list)

    type_counts = Counter(p.get("type", "unknown") for p in pins_list)
    type_str = ", ".join(f"{count} {ptype}" for ptype, count in type_counts.most_common())

    names = " ".join(p.get("name", "").upper() for p in pins_list)

    buses = []
    if re.search(r"\b(SDA|SCL|I2C)\b", names):
        buses.append("I2C")
    if re.search(r"\b(TX|RX|TXD|RXD|UART|USART)\b", names):
        buses.append("UART")
    if re.search(r"\b(MOSI|MISO|SCK|CS|SPI)\b", names):
        buses.append("SPI")
    if re.search(r"\b(CAN|CAN_TX|CAN_RX)\b", names):
        buses.append("CAN")
    if re.search(r"\b(USB_D|D\+|D\-|DP|DN|VBUS)\b", names):
        buses.append("USB")
    if re.search(r"\b(SDIO|SD_CMD|SD_CLK|SD_D[0-9])\b", names):
        buses.append("SDIO")

    bus_str = f"Supports: {', '.join(buses)}" if buses else "No standard buses detected."

    return f"Total Pins: {total_pins} ({type_str}). {bus_str}"
