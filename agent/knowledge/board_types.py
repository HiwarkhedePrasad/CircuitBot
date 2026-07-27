"""Board type knowledge — what each board type provides.

Used by the architecture planner to determine what components are
already built-in and should NOT be added as separate parts.
"""

from typing import Any


BOARD_TYPES: dict[str, dict[str, Any]] = {
    "devkit": {
        "description": "Development board with USB, regulator, debug interface",
        "provides": {
            "usb_to_serial": True,
            "regulator_3v3": True,
            "reset_button": True,
            "boot_button": True,
            "status_led": True,
            "header_pins": True,
        },
        "avoid_adding": [
            "CP2102", "CP2102N", "CH340", "CH340G", "FT232", "FT230",
            "AMS1117", "AP2112", "ME6211",
            "USB_C_Receptacle", "USB_C_", "USB_Receptacle",
            "reset_button", "boot_button",
        ],
        "examples": {
            "ESP32-C3": "ESP32-C3-DevKitC-02",
            "ESP32-S3": "ESP32-S3-DevKitC-1",
            "ESP32-C6": "ESP32-C6-DevKitC-1",
            "STM32F103": "BluePill",
            "STM32F401": "WeAct Black Pill",
            "RP2040": "Raspberry Pi Pico",
            "RP2350": "Raspberry Pi Pico 2",
        },
    },
    "module": {
        "description": "Surface-mount module (e.g. ESP32-C3-MINI-1) with antenna, crystal, flash",
        "provides": {
            "antenna": True,
            "crystal": True,
            "flash": True,
            "regulator_3v3": False,
            "usb_to_serial": False,
        },
        "avoid_adding": [
            "Crystal", "Crystal_GND24", "Crystal_Small",
            "antenna", "flash",
        ],
        "examples": {
            "ESP32-C3": "ESP32-C3-MINI-1",
            "ESP32-S3": "ESP32-S3-MINI-1",
            "ESP32-C6": "ESP32-C6-MINI-1",
        },
    },
    "bare_ic": {
        "description": "Bare MCU chip, requires all support components",
        "provides": {},
        "avoid_adding": [],
        "examples": {
            "ESP32-C3": "ESP32-C3FN4",
            "ESP32-S3": "ESP32-S3FN8",
        },
    },
    "custom_pcb": {
        "description": "Custom PCB design, full control over layout",
        "provides": {},
        "avoid_adding": [],
        "examples": {},
    },
}

# Keywords in the user prompt that indicate each board type
BOARD_TYPE_KEYWORDS: dict[str, list[str]] = {
    "devkit": [
        "devkit", "dev kit", "dev board", "development board",
        "nodemcu", "wemos", "node", "breakout",
    ],
    "module": [
        "module", "mini", "wroom", "mod",
    ],
    "bare_ic": [
        "bare", "chip", "ic", "surface mount", "smd",
        "minimal", "compact", "small",
    ],
}


def infer_board_type_from_prompt(prompt: str) -> str | None:
    """Infer board type from user prompt keywords. Returns None if ambiguous."""
    prompt_lower = prompt.lower()
    scores: dict[str, int] = {}
    for btype, keywords in BOARD_TYPE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in prompt_lower)
        if score > 0:
            scores[btype] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


def get_avoid_list(board_type: str) -> list[str]:
    """Return list of component patterns that should NOT be added for this board type."""
    bt = BOARD_TYPES.get(board_type, {})
    return bt.get("avoid_adding", [])


def get_provides(board_type: str) -> dict[str, bool]:
    """Return dict of capabilities this board type provides (True=builtin, False=not provided)."""
    bt = BOARD_TYPES.get(board_type, {})
    return bt.get("provides", {})
