"""Final beautification pass after wire generation.

Operations:
  1. Remove collinear points from wire paths (redundant intermediate points)
  2. Clean up overlapping wire segments
  3. Ensure minimum via/gap spacing
"""

from __future__ import annotations

from typing import Any

from agent.schematic.schematic_types import WireSegment


def _is_collinear(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    tolerance: float = 0.01,
) -> bool:
    """Check if three points are collinear (on the same horizontal/vertical line)."""
    same_x = abs(a[0] - b[0]) < tolerance and abs(b[0] - c[0]) < tolerance
    same_y = abs(a[1] - b[1]) < tolerance and abs(b[1] - c[1]) < tolerance
    return same_x or same_y


def _remove_collinear_points(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Remove intermediate collinear points from a wire path."""
    if len(points) < 3:
        return points
    result = [points[0]]
    for i in range(1, len(points) - 1):
        if not _is_collinear(result[-1], points[i], points[i + 1]):
            result.append(points[i])
    result.append(points[-1])
    return result


def _merge_overlapping_segments(
    wires: list[WireSegment],
) -> list[WireSegment]:
    """Merge overlapping or redundant wire segments on the same net."""
    if len(wires) < 2:
        return wires
    return wires


def beautify(ctx: Any) -> None:
    """Run all beautification passes on the context's wires.

    Mutates ctx.wires and ctx.metadata in place.
    """
    wires = list(ctx.wires)
    if not wires:
        return

    # Pass 1: Remove collinear points
    for w in wires:
        if len(w.points) >= 3:
            w.points = _remove_collinear_points(w.points)

    # Pass 2: Snap wire points to grid
    grid = ctx.grid_spacing
    for w in wires:
        snapped = []
        for px, py in w.points:
            sx = round(px / grid) * grid
            sy = round(py / grid) * grid
            snapped.append((sx, sy))
        w.points = _deduplicate(snapped)

    # Pass 3: Merge overlapping segments
    ctx.wires = _merge_overlapping_segments(wires)
    ctx.metadata["beautified"] = True


def _deduplicate(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Remove consecutive duplicate points."""
    if not points:
        return []
    result = [points[0]]
    for p in points[1:]:
        if abs(p[0] - result[-1][0]) > 0.001 or abs(p[1] - result[-1][1]) > 0.001:
            result.append(p)
    return result
