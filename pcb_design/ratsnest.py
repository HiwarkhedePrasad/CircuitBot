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


def _build_trace_endpoint_map(model: BoardModel, net_name: str) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Build an endpoint adjacency map for traces on a single net."""
    adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {}
    target_net = net_name.upper()
    for trace in model.traces:
        if trace.net.upper() != target_net:
            continue
        path = trace.path
        if len(path) < 2:
            continue
        start = path[0]
        end = path[-1]
        start_key = (round(start[0], 2), round(start[1], 2))
        end_key = (round(end[0], 2), round(end[1], 2))
        adjacency.setdefault(start_key, []).append(end_key)
        adjacency.setdefault(end_key, []).append(start_key)
    return adjacency


def _connected_pad_groups(
    positions: list[tuple[str, tuple[float, float]]],
    adjacency: dict[tuple[int, int], list[tuple[int, int]]],
) -> list[int]:
    """Return component ids for pads connected through trace endpoint chains."""
    groups: list[int] = [-1] * len(positions)
    point_to_indices: dict[tuple[int, int], list[int]] = {}
    for index, (_, pos) in enumerate(positions):
        key = (round(pos[0], 2), round(pos[1], 2))
        point_to_indices.setdefault(key, []).append(index)

    group_id = 0
    for index, (_, pos) in enumerate(positions):
        if groups[index] != -1:
            continue
        start_key = (round(pos[0], 2), round(pos[1], 2))
        stack = [start_key]
        seen_points = set()
        while stack:
            point = stack.pop()
            if point in seen_points:
                continue
            seen_points.add(point)
            for pad_index in point_to_indices.get(point, []):
                groups[pad_index] = group_id
            for neighbor in adjacency.get(point, []):
                if neighbor not in seen_points:
                    stack.append(neighbor)
        if groups[index] == -1:
            groups[index] = group_id
        group_id += 1
    return groups


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

        # Phase 1: group pads joined by chains of trace endpoints on the same net.
        adjacency = _build_trace_endpoint_map(model, net_name)
        group_of = _connected_pad_groups(positions, adjacency)
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
