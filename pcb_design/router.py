"""PCB routing wrapper and Design Rule Check (DRC).

Wraps the existing A* routing engine with a DRC pass. No external KiCad
tools required — all checks run on the routed traces in Python.
"""

from __future__ import annotations

import math
from typing import Any

from agent.layout_engine import (
    BackendLayoutEngine,
    GRID_SIZE,
    MATRIX_OFFSET,
)

# Default DRC rules (mm)
DRC_RULES: dict[str, float] = {
    "min_clearance": 0.254,
    "min_track_width": 0.254,
    "board_edge_margin": 3.0,
}


# ── Utility ─────────────────────────────────────────────────────────────


def _trace_cells(trace: dict) -> list[tuple[int, int]]:
    """Convert a trace path to grid-cell coordinates."""
    return [
        (round(p["x"] / GRID_SIZE) + MATRIX_OFFSET,
         round(p["y"] / GRID_SIZE) + MATRIX_OFFSET)
        for p in trace.get("path", [])
    ]


def _mm_from_grid(gx: int, gy: int) -> tuple[float, float]:
    return ((gx - MATRIX_OFFSET) * GRID_SIZE,
            (gy - MATRIX_OFFSET) * GRID_SIZE)


def _point_to_bbox_dist(x: float, y: float, bbox: dict, cx: float, cy: float) -> float:
    """Minimum distance from (x, y) to a component bbox.

    bbox = ``{x, y, w, h}`` (offset from component center ``(cx, cy)``).
    """
    left = cx + bbox["x"]
    right = left + bbox["w"]
    top = cy + bbox["y"]
    bottom = top + bbox["h"]
    if left <= x <= right and top <= y <= bottom:
        return 0.0
    dx = 0.0
    if x < left:
        dx = left - x
    elif x > right:
        dx = x - right
    dy = 0.0
    if y < top:
        dy = top - y
    elif y > bottom:
        dy = y - bottom
    return math.sqrt(dx * dx + dy * dy)


# ── DRC checks ──────────────────────────────────────────────────────────


def _check_trace_trace_clearance(
    traces: list[dict],
    rules: dict[str, float],
) -> list[dict]:
    """Find pairs of different-net traces whose cells are too close."""
    violations: list[dict] = []
    min_cl = rules.get("min_clearance", 0.254)
    # Grid-spacing check: at GRID_SIZE=1.27mm, adjacent cells are 1.27mm apart,
    # so only same-cell or manhattan-1 adjacency matters for 0.254mm clearance.
    # Build a map: (gx, gy) -> list of (trace_idx, net_name)
    cell_owners: dict[tuple[int, int], list[tuple[int, str]]] = {}
    for ti, tr in enumerate(traces):
        net = tr.get("net", "")
        for gx, gy in _trace_cells(tr)[2:-2]:  # skip pin cells
            cell_owners.setdefault((gx, gy), []).append((ti, net))

    # Check each cell for conflicts
    for (gx, gy), owners in cell_owners.items():
        if len(owners) < 2:
            continue
        # Different nets sharing the same cell
        unique_nets = set(n for _, n in owners)
        if len(unique_nets) > 1:
            # Also check 8-neighborhood for adjacency violations
            for dx, dy in [(1, 0), (0, 1), (1, 1), (1, -1)]:
                nx, ny = gx + dx, gy + dy
                neighbors = cell_owners.get((nx, ny), [])
                for _, own_net in neighbors:
                    if own_net not in unique_nets:
                        mx, my = _mm_from_grid(gx, gy)
                        violations.append({
                            "type": "clearance",
                            "message": f"Trace-to-trace clearance < {min_cl}mm at ({mx:.2f}, {my:.2f})",
                            "severity": "warning",
                            "location": {"x": mx, "y": my},
                            "nets": list(unique_nets),
                        })
                        break
                else:
                    continue
                break

    # Deduplicate by location
    seen = set()
    deduped = []
    for v in violations:
        key = (round(v["location"]["x"], 1), round(v["location"]["y"], 1))
        if key not in seen:
            seen.add(key)
            deduped.append(v)
    return deduped


def _check_trace_component_clearance(
    traces: list[dict],
    components: list[dict],
    rules: dict[str, float],
) -> list[dict]:
    """Find traces that pass too close to component bodies."""
    violations: list[dict] = []
    min_cl = rules.get("min_clearance", 0.254)

    # Build ref-to-owning-comp map for pin-exclusion
    trace_owners: dict[int, set[str]] = {}
    for ti, tr in enumerate(traces):
        owners: set[str] = set()
        for key in (tr.get("source", ""), tr.get("target", "")):
            ref = key.split(":")[0]
            if ref:
                owners.add(ref)
        trace_owners[ti] = owners

    for ti, tr in enumerate(traces):
        cells = _trace_cells(tr)[2:-2]  # skip pin cells
        for gx, gy in cells:
            mx, my = _mm_from_grid(gx, gy)
            for comp in components:
                ref = comp["ref_des"]
                if ref in trace_owners.get(ti, set()):
                    continue  # skip the trace's own components
                bbox = comp.get("geom_bbox")
                if not bbox:
                    continue
                d = _point_to_bbox_dist(mx, my, bbox, comp["x"], comp["y"])
                if d < min_cl and d >= 0:
                    violations.append({
                        "type": "clearance",
                        "message": (
                            f"Trace {tr.get('source', '?')}->{tr.get('target', '?')} "
                            f"too close to {ref} ({d:.3f}mm < {min_cl}mm)"
                        ),
                        "severity": "warning",
                        "location": {"x": mx, "y": my},
                        "ref": ref,
                        "distance": d,
                    })
                    break  # one violation per trace per component
            else:
                continue
            break  # one violation per trace

    return violations


def _check_board_edge_keepout(
    traces: list[dict],
    components: list[dict],
    rules: dict[str, float],
) -> list[dict]:
    """Check traces don't come too close to the estimated board edge."""
    violations: list[dict] = []
    margin = rules.get("board_edge_margin", 3.0)

    if not components:
        return violations

    # Estimate board area from component placement extents
    min_x = min(c["x"] + c.get("geom_bbox", {}).get("x", -5) for c in components)
    max_x = max(c["x"] + c.get("geom_bbox", {}).get("x", 0) + c.get("geom_bbox", {}).get("w", 10) for c in components)
    min_y = min(c["y"] + c.get("geom_bbox", {}).get("y", -5) for c in components)
    max_y = max(c["y"] + c.get("geom_bbox", {}).get("y", 0) + c.get("geom_bbox", {}).get("h", 10) for c in components)

    for tr in traces:
        for p in tr.get("path", []):
            mx, my = p["x"], p["y"]
            if (mx < min_x + margin or mx > max_x - margin or
                my < min_y + margin or my > max_y - margin):
                violations.append({
                    "type": "board_edge",
                    "message": f"Trace {tr.get('source', '?')}->{tr.get('target', '?')} within {margin}mm of board edge at ({mx:.2f}, {my:.2f})",
                    "severity": "info",
                    "location": {"x": mx, "y": my},
                })
                break  # one per trace

    return violations


# ── Public DRC entry point ──────────────────────────────────────────────


def drc(
    traces: list[dict],
    components: list[dict],
    rules: dict[str, float] | None = None,
) -> list[dict]:
    """Run all DRC checks on a routed design.

    Parameters
    ----------
    traces:
        List of ``{source, target, net, path}`` dicts from the router.
    components:
        Engine's ``.components`` list (each has ``ref_des, x, y, geom_bbox``).
    rules:
        DRC thresholds.  Uses ``DRC_RULES`` defaults when ``None``.

    Returns
    -------
    List of violation dicts ``{type, message, severity, location}``.
    """
    rules = rules or DRC_RULES
    violations: list[dict] = []
    violations.extend(_check_trace_trace_clearance(traces, rules))
    violations.extend(_check_trace_component_clearance(traces, components, rules))
    violations.extend(_check_board_edge_keepout(traces, components, rules))
    return violations


# ── Main routing wrapper ────────────────────────────────────────────────


def route_board(
    engine: BackendLayoutEngine,
    netlist: list[dict],
    pin_matrix: dict,
    rules: dict[str, float] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Run routing + DRC.

    Calls the engine's existing A* router and overlap fixer, then
    runs DRC on the result.

    Returns
    -------
    ``(traces, violations)`` where *traces* is the full routed path list
    and *violations* is the DRC violation list.
    """
    traces = engine.route_traces(netlist, pin_matrix)
    traces, n_fixed, n_conflicts = engine.check_and_fix_overlaps(traces)
    violations = drc(traces, engine.components, rules)
    return traces, violations
