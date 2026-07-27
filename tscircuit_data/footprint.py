"""
Footprint generator — create footprints from text descriptions.

Uses @tscircuit/footprinter DSL or falls back to built-in templates.
No React or rendering dependencies.
"""

import logging
import re
from typing import Optional

from .schema import Footprint

logger = logging.getLogger(__name__)

# Common SMD footprint templates (pad positions in mm)
_TEMPLATES = {
    # Passives
    "0201": {"pads": [{"x": -0.5, "y": 0, "w": 0.6, "h": 0.3}, {"x": 0.5, "y": 0, "w": 0.6, "h": 0.3}]},
    "0402": {"pads": [{"x": -0.8, "y": 0, "w": 1.0, "h": 0.5}, {"x": 0.8, "y": 0, "w": 1.0, "h": 0.5}]},
    "0603": {"pads": [{"x": -1.0, "y": 0, "w": 1.5, "h": 0.8}, {"x": 1.0, "y": 0, "w": 1.5, "h": 0.8}]},
    "0805": {"pads": [{"x": -1.3, "y": 0, "w": 2.0, "h": 1.0}, {"x": 1.3, "y": 0, "w": 2.0, "h": 1.0}]},
    "1206": {"pads": [{"x": -1.8, "y": 0, "w": 3.2, "h": 1.6}, {"x": 1.8, "y": 0, "w": 3.2, "h": 1.6}]},
    "1210": {"pads": [{"x": -1.8, "y": 0, "w": 3.2, "h": 2.5}, {"x": 1.8, "y": 0, "w": 3.2, "h": 2.5}]},

    # IC packages
    "sot23": {"pads": [
        {"x": -0.95, "y": -1.0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 0.5, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": -0.5, "w": 0.6, "h": 0.5},
    ]},
    "sot-23": {"pads": [
        {"x": -0.95, "y": -1.0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 0.5, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": -0.5, "w": 0.6, "h": 0.5},
    ]},
    "sot-23-6": {"pads": [
        {"x": -0.95, "y": -1.0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": -1.0, "w": 0.6, "h": 0.5},
    ]},
    "sot23-6": {"pads": [
        {"x": -0.95, "y": -1.0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": -0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 1.0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": 0, "w": 0.6, "h": 0.5},
        {"x": 0.95, "y": -1.0, "w": 0.6, "h": 0.5},
    ]},
    "soic-8": {"pads": [
        {"x": -1.9, "y": -1.27, "w": 0.6, "h": 1.2},
        {"x": -1.9, "y": 0, "w": 0.6, "h": 1.2},
        {"x": -1.9, "y": 1.27, "w": 0.6, "h": 1.2},
        {"x": -1.9, "y": 2.54, "w": 0.6, "h": 1.2},
        {"x": 1.9, "y": 2.54, "w": 0.6, "h": 1.2},
        {"x": 1.9, "y": 1.27, "w": 0.6, "h": 1.2},
        {"x": 1.9, "y": 0, "w": 0.6, "h": 1.2},
        {"x": 1.9, "y": -1.27, "w": 0.6, "h": 1.2},
    ]},
    "qfn-32": {"pads": [
        # Simplified QFN-32 (7x7mm)
        *[{"x": -2.4 + i * 0.8, "y": -2.4, "w": 0.5, "h": 0.7} for i in range(8)],
        *[{"x": 2.4, "y": -2.4 + i * 0.8, "w": 0.7, "h": 0.5} for i in range(8)],
        *[{"x": 2.4 - i * 0.8, "y": 2.4, "w": 0.5, "h": 0.7} for i in range(8)],
        *[{"x": -2.4, "y": 2.4 - i * 0.8, "w": 0.7, "h": 0.5} for i in range(8)],
    ]},

    # Connectors
    "usb-c": {"pads": [
        {"x": -3.25, "y": -1.0, "w": 0.5, "h": 0.6},
        {"x": -2.55, "y": -1.0, "w": 0.5, "h": 0.6},
        {"x": -1.85, "y": -1.0, "w": 0.5, "h": 0.6},
        {"x": -1.15, "y": -1.0, "w": 0.5, "h": 0.6},
        {"x": -3.25, "y": 1.0, "w": 0.5, "h": 0.6},
        {"x": -2.55, "y": 1.0, "w": 0.5, "h": 0.6},
        {"x": -1.85, "y": 1.0, "w": 0.5, "h": 0.6},
        {"x": -1.15, "y": 1.0, "w": 0.5, "h": 0.6},
        # Shield pads
        {"x": -3.5, "y": 0, "w": 1.0, "h": 2.0},
        {"x": 3.5, "y": 0, "w": 1.0, "h": 2.0},
    ]},
    "header-2.54mm": {"pads": [
        # 1x4 header example
        {"x": 0, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 2.54, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 5.08, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 7.62, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
    ]},
    "header": {"pads": [
        # 1x4 header example
        {"x": 0, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 2.54, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 5.08, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
        {"x": 7.62, "y": 0, "w": 1.5, "h": 1.5, "drill": 0.8},
    ]},
    "conn": {"pads": [
        # 2-pin connector
        {"x": 0, "y": 0, "w": 2.0, "h": 2.0, "drill": 1.0},
        {"x": 2.54, "y": 0, "w": 2.0, "h": 2.0, "drill": 1.0},
    ]},
}


def generate_footprint(description: str) -> Optional[Footprint]:
    """Generate a footprint from a text description.

    Args:
        description: Footprint description (e.g. "0805", "SOT-23-6", "USB-C")

    Returns:
        Footprint object or None if unrecognized
    """
    normalized = _normalize_footprint_name(description)

    # Check built-in templates (prefer longest match)
    best_match = None
    best_len = 0
    for key, template in _TEMPLATES.items():
        if key in normalized and len(key) > best_len:
            best_match = (key, template)
            best_len = len(key)

    if best_match:
        key, template = best_match
        pads = []
        for i, p in enumerate(template["pads"]):
            pads.append({
                "number": str(i + 1),
                "type": "smd" if p.get("drill") is None else "thru_hole",
                "x": p["x"],
                "y": p["y"],
                "width": p.get("w", 1.0),
                "height": p.get("h", 1.0),
                "drill": p.get("drill"),
            })
        return Footprint(
            name=description,
            pads=pads,
            layers=["F.Cu", "F.Paste", "F.Mask", "F.SilkS"],
        )

    # Try tscircuit API
    try:
        from .client import TscircuitClient
        client = TscircuitClient(cache_enabled=True)
        return client.get_footprint(description)
    except Exception as e:
        logger.debug(f"tscircuit footprint lookup failed: {e}")

    return None


def get_footprint_names() -> list[str]:
    """Get list of available built-in footprint names."""
    return list(_TEMPLATES.keys())


def _normalize_footprint_name(name: str) -> str:
    """Normalize a footprint name for lookup."""
    name = name.lower().strip()
    name = re.sub(r'[_\s]+', '-', name)
    name = re.sub(r'[^a-z0-9\-]', '', name)
    return name
