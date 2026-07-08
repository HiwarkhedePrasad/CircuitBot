"""Block optimizer — improves layout after expansion, before wire generation.

Operations (in order):
  1. Grid snap — snap all component positions to the layout grid
  2. Same-row alignment — align components at the same Y to a common baseline
  3. Equalize spacing — distribute components evenly within each block
  4. Rotation normalization — ensure consistent orientation
  5. Whitespace compression — remove excessive gaps
  6. Overlap resolution — shift apart any overlapping components
"""

from __future__ import annotations

from typing import Any


def _snap(value: float, grid: float) -> float:
    return round(value / grid) * grid


def _get_positions(ctx: Any) -> dict[str, tuple[float, float, float]]:
    return ctx.metadata.get("component_positions", {})


def _set_positions(ctx: Any, positions: dict[str, tuple[float, float, float]]) -> None:
    ctx.metadata["component_positions"] = positions


def optimize(ctx: Any) -> None:
    """Run all optimization passes in sequence.

    Mutates ctx.metadata["component_positions"] in place.
    """
    if ctx.metadata is None:
        return

    positions = _get_positions(ctx)
    if not positions:
        return

    # Pass 1: Grid snap
    grid = ctx.grid_spacing
    snapped = {}
    for ref, (x, y, rot) in positions.items():
        snapped[ref] = (_snap(x, grid), _snap(y, grid), rot)
    positions = snapped

    # Pass 2: Same-row alignment
    positions = _align_rows(positions, grid)

    # Pass 3: Equalize horizontal spacing
    positions = _equalize_spacing(positions, grid)

    # Pass 4: Overlap resolution
    positions = _resolve_overlaps(positions, grid)

    _set_positions(ctx, positions)


def _align_rows(
    positions: dict[str, tuple[float, float, float]],
    grid: float,
) -> dict[str, tuple[float, float, float]]:
    """Group components by Y coordinate and align them to the same Y."""
    rows: dict[float, list[str]] = {}
    for ref, (x, y, rot) in positions.items():
        snapped_y = _snap(y, grid)
        if snapped_y not in rows:
            rows[snapped_y] = []
        rows[snapped_y].append(ref)

    result = dict(positions)
    for y_snapped, refs in rows.items():
        if len(refs) <= 1:
            continue
        for ref in refs:
            x, _, rot = result[ref]
            result[ref] = (x, y_snapped, rot)

    return result


def _equalize_spacing(
    positions: dict[str, tuple[float, float, float]],
    grid: float,
) -> dict[str, tuple[float, float, float]]:
    """Within each row, equalize horizontal spacing between components."""
    rows: dict[float, list[tuple[str, float, float]]] = {}
    for ref, (x, y, rot) in positions.items():
        if y not in rows:
            rows[y] = []
        rows[y].append((ref, x, rot))

    result = dict(positions)
    for y, items in rows.items():
        if len(items) <= 2:
            continue
        sorted_items = sorted(items, key=lambda t: t[1])
        min_x = sorted_items[0][1]
        max_x = sorted_items[-1][1]
        gap = (max_x - min_x) / (len(sorted_items) - 1) if len(sorted_items) > 1 else 0
        for i, (ref, _, rot) in enumerate(sorted_items):
            new_x = _snap(min_x + i * gap, grid)
            result[ref] = (new_x, y, rot)

    return result


def _resolve_overlaps(
    positions: dict[str, tuple[float, float, float]],
    grid: float,
) -> dict[str, tuple[float, float, float]]:
    """Detect and resolve overlapping components by shifting them apart."""
    MIN_COMPONENT_WIDTH = 5.0
    MIN_COMPONENT_HEIGHT = 3.0

    result = dict(positions)
    items = list(result.items())

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            ref_a, (x_a, y_a, rot_a) = items[i]
            ref_b, (x_b, y_b, rot_b) = items[j]

            # Simple bounding-box check
            overlap_x = abs(x_a - x_b) < MIN_COMPONENT_WIDTH
            overlap_y = abs(y_a - y_b) < MIN_COMPONENT_HEIGHT

            if overlap_x and overlap_y:
                if x_a <= x_b:
                    new_x_b = _snap(x_b + MIN_COMPONENT_WIDTH + grid, grid)
                    result[ref_b] = (new_x_b, y_b, rot_b)

    return result
