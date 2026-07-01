"""MST-based ratsnest generator for PCB boards.

Computes straight-line "airwire" edges between unconnected pads on the
same net that still need a trace.  Uses Kruskal's MST on the set of pad
positions per net, collapsing any pads already joined by copper into a
single group before the MST pass.
"""

from __future__ import annotations

import math
from typing import Optional

from pcb_design.board_model import BoardModel


def _union_find(n: int) -> tuple[list[int], list[int]]:
    parent = list(range(n))
    rank = [0] * n
    return parent, rank


def _find(parent: list[int], x: int) -> int:
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _union(parent: list[int], rank: list[int], a: int, b: int) -> None:
    ra, rb = _find(parent, a), _find(parent, b)
    if ra == rb:
        return
    if rank[ra] < rank[rb]:
        parent[ra] = rb
    elif rank[ra] > rank[rb]:
        parent[rb] = ra
    else:
        parent[rb] = ra
        rank[ra] += 1


def _pad_position(model: BoardModel, pin_key: str) -> Optional[tuple[float, float]]:
    """Resolve a pin key like ``"U1:3"`` to an absolute board-space coordinate."""
    ref, _, pnum = pin_key.partition(":")
    comp = model.component_at(ref)
    if comp is None:
        return None
    pad = next((p for p in comp.pads if str(p.number) == pnum), None)
    if pad is None:
        return None
    angle = math.radians(comp.rotation + (pad.rotation or 0))
    rx = pad.x * math.cos(angle) - pad.y * math.sin(angle)
    ry = pad.x * math.sin(angle) + pad.y * math.cos(angle)
    return (comp.x + rx, comp.y + ry)


def _pads_already_connected(
    model: BoardModel,
    net_name: str,
    pin_a: str,
    pin_b: str,
) -> bool:
    """Return True if *pin_a* and *pin_b* are already joined by copper traces."""
    pos_a = _pad_position(model, pin_a)
    pos_b = _pad_position(model, pin_b)
    if pos_a is None or pos_b is None:
        return False
    for trace in model.traces:
        if trace.net.upper() != net_name.upper():
            continue
        path = trace.path
        if len(path) < 2:
            continue
        start = path[0]
        end = path[-1]
        # Check if trace endpoints match our pad positions
        da1 = math.hypot(start[0] - pos_a[0], start[1] - pos_a[1])
        db1 = math.hypot(end[0] - pos_b[0], end[1] - pos_b[1])
        if da1 < 0.01 and db1 < 0.01:
            return True
        da2 = math.hypot(end[0] - pos_a[0], end[1] - pos_a[1])
        db2 = math.hypot(start[0] - pos_b[0], start[1] - pos_b[1])
        if da2 < 0.01 and db2 < 0.01:
            return True
    return False


def compute_ratsnest(model: BoardModel) -> dict[str, list[dict]]:
    """Compute MST ratsnest edges for every net in the board.

    Returns a dict mapping net names to lists of edge dicts::

        {"GND": [{"x1": 1.0, "y1": 2.0, "x2": 3.0, "y2": 4.0}, ...], ...}

    Fully routed nets (all pads connected) and single-pin nets produce
    no edges.
    """
    result: dict[str, list[dict]] = {}

    for net_entry in model.nets:
        net_name = net_entry.get("name", "") or net_entry.get("net", "")
        if not net_name:
            continue
        pin_keys = net_entry.get("pins", [])
        if len(pin_keys) < 2:
            continue

        # Resolve positions, drop unresolvable pins
        positions: list[tuple[str, tuple[float, float]]] = []
        for pk in pin_keys:
            pos = _pad_position(model, pk)
            if pos is not None:
                positions.append((pk, pos))

        n = len(positions)
        if n < 2:
            continue

        # Phase 1: union-find groups for pads already joined by traces
        parent, rank = _union_find(n)
        for i in range(n):
            for j in range(i + 1, n):
                if _pads_already_connected(model, net_name, positions[i][0], positions[j][0]):
                    _union(parent, rank, i, j)

        # Count unique groups
        group_of = [_find(parent, i) for i in range(n)]
        unique_groups = set(group_of)
        if len(unique_groups) < 2:
            continue  # fully routed

        # Phase 2: collapse each group to a representative point (first pad in group)
        rep_of: dict[int, int] = {}  # group_root → index of representative
        for i in range(n):
            g = group_of[i]
            if g not in rep_of:
                rep_of[g] = i

        rep_indices = list(rep_of.values())
        m = len(rep_indices)
        if m < 2:
            continue

        # Phase 3: Kruskal's MST on representatives using Euclidean distance
        edges = []
        for a_idx in range(m):
            ia = rep_indices[a_idx]
            _, pa = positions[ia]
            for b_idx in range(a_idx + 1, m):
                ib = rep_indices[b_idx]
                _, pb = positions[ib]
                dist = math.hypot(pa[0] - pb[0], pa[1] - pb[1])
                edges.append((dist, a_idx, b_idx))

        edges.sort(key=lambda e: e[0])
        p2, r2 = _union_find(m)
        mst_edges: list[tuple[int, int]] = []
        for d, a, b in edges:
            if _find(p2, a) != _find(p2, b):
                _union(p2, r2, a, b)
                mst_edges.append((a, b))

        # Phase 4: emit edges expanded back to the actual pad coordinates
        net_edges: list[dict] = []
        for a_idx, b_idx in mst_edges:
            ia = rep_indices[a_idx]
            ib = rep_indices[b_idx]
            _, p_a = positions[ia]
            _, p_b = positions[ib]
            net_edges.append({
                "x1": p_a[0], "y1": p_a[1],
                "x2": p_b[0], "y2": p_b[1],
            })

        if net_edges:
            result[net_name] = net_edges

    return result
