"""Configurable weight dicts for placement and routing scoring.

These weights determine how much each metric contributes to the total score.
Lower weights can still dominate if metric values span a large range.

Tuning guide:
    - ``overlap`` and ``disconnected_pins`` are **hard constraints** — they
      should dominate all other terms so the optimizer prioritizes zero overlaps
      and zero disconnections over everything else.
    - ``crossing`` terms are moderate — readable schematics≠zero crossings but
      minimizing them is important.
    - ``wire_length`` and ``bends`` are soft preferences — shorter is better
      but not at the expense of overlaps or disconnections.
"""

from __future__ import annotations

from typing import Any

PLACEMENT_WEIGHTS: dict[str, float] = {
    "overlap": 10000.0,
    "block_distance": 300.0,
    "estimated_crossings": 5000.0,
    "estimated_wire_length": 50.0,
    "signal_flow": 100.0,
    "pin_direction": 1000.0,
    "alignment": 5.0,
}

ROUTING_WEIGHTS: dict[str, float] = {
    "disconnected_pins": 100000.0,
    "actual_crossings": 5000.0,
    "actual_wire_length": 50.0,
    "wire_bends": 20.0,
}


def merge_weights(base: dict[str, float], overrides: dict[str, Any] | None) -> dict[str, float]:
    """Return a copy of *base* with any *overrides* applied."""
    w = dict(base)
    if overrides:
        w.update({k: float(v) for k, v in overrides.items() if k in w})
    return w
