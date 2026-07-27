"""
KiCad ↔ CircuitJSON converter.

Converts KiCad symbols and footprints to CircuitJSON format and vice versa.
This allows CircuitBot to bridge between its existing KiCad RAG and tscircuit data.
"""

import re
import logging
from typing import Optional

from .schema import Component, Symbol, Footprint, Pin

logger = logging.getLogger(__name__)


def kicad_symbol_to_component(kicad_text: str, lib_id: str = "") -> Optional[Component]:
    """Parse a KiCad .kicad_sym string into a Component object.

    Args:
        kicad_text: Raw KiCad symbol S-expression text
        lib_id: Library identifier (e.g. "Device:R")

    Returns:
        Component object or None on parse failure
    """
    try:
        name = _extract_field(kicad_text, "Reference") or lib_id.split(":")[-1]
        value = _extract_field(kicad_text, "Value") or ""
        description = _extract_field(kicad_text, "ki_description") or ""
        datasheet = _extract_field(kicad_text, "Datasheet") or ""

        pins = []
        pin_pattern = re.compile(
            r'\(pin\s+(\w+)\s+(\w+)\s+\(at\s+([\d.-]+)\s+([\d.-]+)\s+([\d.-]+)\)'
            r'\s+\(name\s+"([^"]*)"\)\s+\(number\s+"([^"]*)"\)',
            re.DOTALL
        )
        for m in pin_pattern.finditer(kicad_text):
            pins.append(Pin(
                number=m.group(7),
                name=m.group(6),
                type=m.group(1),
                side=_pin_side(float(m.group(5))),
            ))

        category = _guess_category(lib_id, name, description)

        return Component(
            id_str=lib_id,
            name=value or name,
            description=description,
            category=category,
            library=lib_id.split(":")[0] if ":" in lib_id else "",
            symbol=Symbol(lib_id=lib_id, name=name, pins=pins),
            pins=pins,
            datasheet=datasheet,
        )

    except Exception as e:
        logger.warning(f"Failed to parse KiCad symbol: {e}")
        return None


def kicad_footprint_to_footprint(kicad_text: str, name: str = "") -> Optional[Footprint]:
    """Parse a KiCad .kicad_mod string into a Footprint object.

    Args:
        kicad_text: Raw KiCad footprint S-expression text
        name: Footprint name

    Returns:
        Footprint object or None
    """
    try:
        pads = []
        pad_pattern = re.compile(
            r'\(pad\s+"?(\w+)"?\s+(\w+)\s+\(at\s+([\d.-]+)\s+([\d.-]+)(?:\s+([\d.-]+))?\)',
            re.DOTALL
        )
        for m in pad_pattern.finditer(kicad_text):
            pads.append({
                "number": m.group(1),
                "type": m.group(2),
                "x": float(m.group(3)),
                "y": float(m.group(4)),
                "rotation": float(m.group(5)) if m.group(5) else 0,
            })

        layers = re.findall(r'\(layers\s+([^)]+)\)', kicad_text)
        layer_list = layers[0].split() if layers else []

        fp_name = name or _extract_kicad_field(kicad_text, "fp_name") or ""

        return Footprint(
            name=fp_name,
            pads=pads,
            layers=layer_list,
        )

    except Exception as e:
        logger.warning(f"Failed to parse KiCad footprint: {e}")
        return None


def component_to_kicad_symbol(comp: Component) -> str:
    """Convert a Component object to KiCad .kicad_sym format.

    Args:
        comp: Component with symbol and pin data

    Returns:
        KiCad symbol S-expression string
    """
    lines = ['(kicad_symbol (version 20230121) (generator "circuitbot")']
    lines.append(f'  (lib_id "{comp.id_str}")')

    if comp.name:
        lines.append(f'  (property "Reference" "{comp.symbol.name if comp.symbol else "?"}"')
        lines.append(f'    (at 0 0 0)')
        lines.append(f'    (effects (font (size 1.27 1.27))))')

    if comp.name:
        lines.append(f'  (property "Value" "{comp.name}"')
        lines.append(f'    (at 0 -2.54 0)')
        lines.append(f'    (effects (font (size 1.27 1.27))))')

    if comp.description:
        lines.append(f'  (property "ki_description" "{comp.description}"')
        lines.append(f'    (at 0 0 0)')
        lines.append(f'    (effects (font (size 1.27 1.27)) hide))')

    if comp.datasheet:
        lines.append(f'  (property "Datasheet" "{comp.datasheet}"')
        lines.append(f'    (at 0 0 0)')
        lines.append(f'    (effects (font (size 1.27 1.27)) hide))')

    # Add pins
    for i, pin in enumerate(comp.pins):
        x, y = _pin_position(i, len(comp.pins))
        angle = _pin_angle(pin.side)
        lines.append(f'  (pin {pin.type} line (at {x} {y} {angle})')
        lines.append(f'    (length 2.54)')
        lines.append(f'    (name "{pin.name}")')
        lines.append(f'    (number "{pin.number}"))')

    lines.append(')')
    return "\n".join(lines)


def component_to_kicad_footprint(comp: Component) -> str:
    """Convert a Component to a minimal KiCad footprint.

    Args:
        comp: Component with footprint data

    Returns:
        KiCad .kicad_mod string
    """
    fp = comp.footprint
    if not fp:
        return ""

    lines = ['(kicad_mod (version 20221018) (generator "circuitbot")']
    lines.append(f'  (layer "F.Cu")')
    lines.append(f'  (attr smd)')
    lines.append(f'  (fp_text reference REF** (at 0 -2) (layer "F.SilkS")')
    lines.append(f'    (effects (font (size 1 1))))')
    lines.append(f'  (fp_text value "{comp.name}" (at 0 2) (layer "F.Fab")')
    lines.append(f'    (effects (font (size 1 1))))')

    for pad in fp.pads:
        x = pad.get("x", 0)
        y = pad.get("y", 0)
        pad_type = pad.get("type", "smd")
        num = pad.get("number", "1")
        lines.append(f'  (pad "{num}" smd roundrect (at {x} {y})')
        lines.append(f'    (size 1.6 0.8) (layers "F.Cu" "F.Paste" "F.Mask"))')

    lines.append(')')
    return "\n".join(lines)


# ── Internal helpers ────────────────────────────────────────────────────

def _extract_field(text: str, field_name: str) -> Optional[str]:
    """Extract a named field value from KiCad S-expression."""
    pattern = rf'\(property\s+"{re.escape(field_name)}"\s+"([^"]*)"'
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _extract_kicad_field(text: str, field_name: str) -> Optional[str]:
    """Extract a field from KiCad format."""
    pattern = rf'\({re.escape(field_name)}\s+"([^"]*)"'
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _pin_side(angle: float) -> str:
    """Convert pin angle to side name."""
    angle = angle % 360
    if 315 <= angle or angle < 45:
        return "right"
    elif 45 <= angle < 135:
        return "top"
    elif 135 <= angle < 225:
        return "left"
    else:
        return "bottom"


def _pin_position(index: int, total: int) -> tuple:
    """Calculate pin position based on index."""
    if total <= 4:
        positions = [(-5, 0), (5, 0), (0, -5), (0, 5)]
    else:
        per_side = max(1, total // 4)
        side = index // per_side
        offset = index % per_side
        if side == 0:   # left
            x, y = -5, (offset - per_side / 2) * 2.54
        elif side == 1:  # top
            x, y = (offset - per_side / 2) * 2.54, -5
        elif side == 2:  # right
            x, y = 5, (offset - per_side / 2) * 2.54
        else:            # bottom
            x, y = (offset - per_side / 2) * 2.54, 5
        positions = [(x, y)]
        return positions[0] if positions else (0, 0)

    return positions[index % len(positions)]


def _pin_angle(side: str) -> float:
    """Get pin angle for a given side."""
    return {"right": 0, "top": 90, "left": 180, "bottom": 270}.get(side, 0)


def _guess_category(lib_id: str, name: str, description: str) -> str:
    """Guess component category from identifiers."""
    text = f"{lib_id} {name} {description}".lower()
    if any(k in text for k in ["resistor", "r_small", ":r_"]):
        return "resistor"
    if any(k in text for k in ["capacitor", "c_small", ":c_"]):
        return "capacitor"
    if any(k in text for k in ["inductor", "l_small", ":l_"]):
        return "inductor"
    if any(k in text for k in ["led", "diode", ":d_"]):
        return "diode"
    if any(k in text for k in ["connector", "usb", "jack", "header"]):
        return "connector"
    if any(k in text for k in ["mcu", "esp32", "stm32", "rp2040", "processor"]):
        return "ic"
    if any(k in text for k in ["regulator", "ldo", "buck", "converter"]):
        return "ic"
    if any(k in text for k in ["op-amp", "opamp", "comparator", "amplifier"]):
        return "ic"
    if any(k in text for k in ["transistor", "mosfet", "bjt", "fet"]):
        return "transistor"
    if any(k in text for k in ["crystal", "oscillator", "xtal"]):
        return "crystal"
    return "other"
