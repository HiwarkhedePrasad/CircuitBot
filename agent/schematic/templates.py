"""Motif expansion templates — deterministic layouts per motif type.

Each template defines component positions and internal wires as offsets
relative to the anchor component.  The same logical template can produce
multiple variants (default, horizontal, vertical, mirrored).

TemplateFactory provides instantiation with optional variant selection.
"""

from __future__ import annotations

from typing import Optional

from agent.schematic.schematic_types import (
    MotifType,
    TemplateComponent,
    TemplateLayout,
    TemplateWire,
)


# ── Template definitions ─────────────────────────────────────────────────────


def _decoupling_cap() -> TemplateLayout:
    """Capacitor placed below the power pin it decouples.

    Pin 1 (power side) at top, pin 2 (ground side) at bottom.
    """
    return TemplateLayout(
        motif_type=MotifType.DECOUPLING_CAP,
        variant="default",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:1", to_pin="primary:2",
                         path_offsets=[(0, 0), (0, -5.08)]),
        ],
    )


def _pull_up() -> TemplateLayout:
    """Resistor vertical — signal on top, VCC on bottom."""
    return TemplateLayout(
        motif_type=MotifType.PULL_UP,
        variant="vertical",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:1", to_pin="primary:2",
                         path_offsets=[(0, 0), (0, -5.08)]),
        ],
    )


def _pull_down() -> TemplateLayout:
    """Resistor vertical — signal on top, GND on bottom."""
    return TemplateLayout(
        motif_type=MotifType.PULL_DOWN,
        variant="vertical",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:1", to_pin="primary:2",
                         path_offsets=[(0, 0), (0, -5.08)]),
        ],
    )


def _rc_filter() -> TemplateLayout:
    """RC filter: resistor horizontal, capacitor below."""
    return TemplateLayout(
        motif_type=MotifType.RC_FILTER,
        variant="default",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
            TemplateComponent(ref="filter_capacitor", offset_x=0, offset_y=-10.16, rotation=0),
        ],
        wires=[
            # Resistor pin 2 connects to capacitor pin 1
            TemplateWire(from_pin="primary:2", to_pin="filter_capacitor:1",
                         path_offsets=[(5.08, 0), (5.08, -5.08), (0, -5.08)]),
            # Capacitor pin 2 to ground
            TemplateWire(from_pin="filter_capacitor:2", to_pin="",
                         path_offsets=[(0, -7.62)]),
        ],
    )


def _led_indicator() -> TemplateLayout:
    """LED indicator: resistor inline with LED, signal → R → LED → GND."""
    return TemplateLayout(
        motif_type=MotifType.LED_INDICATOR,
        variant="horizontal",
        components=[
            TemplateComponent(ref="current_limit_resistor", offset_x=0, offset_y=0, rotation=0),
            TemplateComponent(ref="primary", offset_x=10.16, offset_y=0, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="current_limit_resistor:2", to_pin="primary:1",
                         path_offsets=[(5.08, 0), (10.16, 0)]),
            TemplateWire(from_pin="primary:2", to_pin="",
                         path_offsets=[(15.24, 0)]),
        ],
    )


def _crystal() -> TemplateLayout:
    """Crystal with two load capacitors below."""
    return TemplateLayout(
        motif_type=MotifType.CRYSTAL,
        variant="default",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
            TemplateComponent(ref="load_cap_1", offset_x=-5.08, offset_y=-7.62, rotation=0),
            TemplateComponent(ref="load_cap_2", offset_x=5.08, offset_y=-7.62, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:1", to_pin="load_cap_1:1",
                         path_offsets=[(-2.54, 0), (-2.54, -3.81), (-5.08, -3.81)]),
            TemplateWire(from_pin="primary:2", to_pin="load_cap_2:1",
                         path_offsets=[(2.54, 0), (2.54, -3.81), (5.08, -3.81)]),
            TemplateWire(from_pin="load_cap_1:2", to_pin="",
                         path_offsets=[(-5.08, -10.16)]),
            TemplateWire(from_pin="load_cap_2:2", to_pin="",
                         path_offsets=[(5.08, -10.16)]),
        ],
    )


def _voltage_divider() -> TemplateLayout:
    """Two resistors stacked vertically."""
    return TemplateLayout(
        motif_type=MotifType.VOLTAGE_DIVIDER,
        variant="vertical",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
            TemplateComponent(ref="second_resistor", offset_x=0, offset_y=-7.62, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:2", to_pin="second_resistor:1",
                         path_offsets=[(0, -3.81)]),
            TemplateWire(from_pin="second_resistor:2", to_pin="",
                         path_offsets=[(0, -10.16)]),
        ],
    )


def _reset_circuit() -> TemplateLayout:
    """Pull-up resistor with optional switch."""
    return TemplateLayout(
        motif_type=MotifType.RESET_CIRCUIT,
        variant="default",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
        ],
        wires=[
            TemplateWire(from_pin="primary:1", to_pin="primary:2",
                         path_offsets=[(0, 0), (0, -5.08)]),
        ],
    )


def _power_entry() -> TemplateLayout:
    """Connector → fuse → TVS → bulk cap chain."""
    return TemplateLayout(
        motif_type=MotifType.POWER_ENTRY,
        variant="horizontal",
        components=[
            TemplateComponent(ref="primary", offset_x=0, offset_y=0, rotation=0),
            TemplateComponent(ref="fuse", offset_x=10.16, offset_y=0, rotation=0),
            TemplateComponent(ref="tvs", offset_x=20.32, offset_y=0, rotation=0),
            TemplateComponent(ref="bulk_cap", offset_x=20.32, offset_y=-7.62, rotation=0),
        ],
        wires=[],
    )


# ── Template registry ────────────────────────────────────────────────────────

_TEMPLATE_REGISTRY: dict[MotifType, dict[str, TemplateLayout]] = {
    MotifType.DECOUPLING_CAP: {"default": _decoupling_cap()},
    MotifType.PULL_UP: {"vertical": _pull_up(), "default": _pull_up()},
    MotifType.PULL_DOWN: {"vertical": _pull_down(), "default": _pull_down()},
    MotifType.RC_FILTER: {"default": _rc_filter()},
    MotifType.LED_INDICATOR: {"horizontal": _led_indicator(), "default": _led_indicator()},
    MotifType.CRYSTAL: {"default": _crystal()},
    MotifType.VOLTAGE_DIVIDER: {"vertical": _voltage_divider(), "default": _voltage_divider()},
    MotifType.RESET_CIRCUIT: {"default": _reset_circuit()},
    MotifType.POWER_ENTRY: {"horizontal": _power_entry(), "default": _power_entry()},
}


def get_template(motif_type: MotifType, variant: str = "default") -> Optional[TemplateLayout]:
    """Get a template layout for a given motif type and variant."""
    variants = _TEMPLATE_REGISTRY.get(motif_type)
    if variants is None:
        return None
    return variants.get(variant)


def list_variants(motif_type: MotifType) -> list[str]:
    """List available variants for a motif type."""
    variants = _TEMPLATE_REGISTRY.get(motif_type, {})
    return list(variants.keys())


def has_template(motif_type: MotifType) -> bool:
    """Check if a template exists for the given motif type."""
    return motif_type in _TEMPLATE_REGISTRY
