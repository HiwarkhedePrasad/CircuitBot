"""Net classification — decides how each net connects: WIRE, LABEL, or GLOBAL.

Avoids importing from agent.utils at module level to prevent pulling in
langchain_openai and other heavy dependencies during test collection.
"""

from __future__ import annotations

WIRE = "wire"
LABEL = "label"
GLOBAL = "global"
HIERARCHICAL = "hierarchical"

LABEL_THRESHOLD = 80.0

_GND_NET_NAMES = {"GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EP", "EPAD", "0V", "SHIELD"}
_POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "V+", "V-", "VSYS", "VOUT", "VEE", "PWR"}

_BUS_PATTERNS = frozenset({
    "I2C", "I²C", "SDA", "SCL",
    "SPI", "MOSI", "MISO", "SCK", "CS", "SS", "NSS",
    "UART", "TX", "RX", "RTS", "CTS",
    "USB", "D+", "D-", "DP", "DM", "VBUS",
    "I2S", "BCLK", "LRCLK", "DIN", "DOUT",
    "SDIO", "CMD", "CLK", "DAT0", "DAT1", "DAT2", "DAT3",
    "CAN", "CANH", "CANL",
    "JTAG", "TMS", "TCK", "TDI", "TDO",
    "SWD", "SWDIO", "SWCLK",
})


def _is_gnd_net(name: str) -> bool:
    return name.upper().lstrip('+') in _GND_NET_NAMES


def _is_power_net(name: str) -> bool:
    n = name.upper().lstrip('+')
    if n in _POWER_NET_NAMES:
        return True
    import re
    if re.match(r'^\d+V\d*$', n) or re.match(r'^V\d+$', n):
        return True
    return False


def _is_passive(id_str: str, category: str) -> bool:
    cat = (category or '').upper()
    return id_str.startswith('Device:') or cat in ('DEVICE',)


def _is_bus_signal(net_name: str) -> bool:
    name = net_name.upper().lstrip('+').strip()
    for pattern in _BUS_PATTERNS:
        if pattern in name or name in pattern:
            return True
    return False


def _is_active_ic(comp: dict) -> bool:
    id_str = comp.get("id_str", "")
    category = comp.get("category", "")
    if _is_passive(id_str, category):
        return False
    if id_str.startswith("Connector:") or (category or "").upper() == "CONNECTOR":
        return False
    if id_str.startswith("power:"):
        return False
    return True


def _estimate_span(pins: list[str], placements: dict[str, dict]) -> float:
    if len(pins) < 2:
        return 0.0
    xs = []
    ys = []
    for pk in pins:
        ref = pk.split(":")[0]
        p = placements.get(ref)
        if p:
            xs.append(p.get("x", 0))
            ys.append(p.get("y", 0))
    if not xs:
        return 0.0
    return max(xs) - min(xs) + max(ys) - min(ys)


def classify_strategy(
    net_name: str,
    pins: list[str],
    components: list[dict],
    placements: dict[str, dict],
) -> str:
    if _is_gnd_net(net_name) or _is_power_net(net_name):
        return GLOBAL

    if _is_bus_signal(net_name):
        return LABEL

    # Reference-based net connectivity: non-adjacent nets spanning > 15mm use net labels
    span = _estimate_span(pins, placements)
    if span > LABEL_THRESHOLD:
        return LABEL

    return WIRE
