"""Legacy tier-column placement engine.

Simple, deterministic placement: components arranged in left-to-right tier
columns with grid packing (max N per column). Satellites placed adjacent
to their parent IC.

Usage::

    from agent.placement.legacy import LegacyPlacer
    placer = LegacyPlacer()
    placements = placer.place(components, netlist, pin_matrix)
"""

from __future__ import annotations

import math

from agent.placement.blocks_v2 import (
    _snap, _tier, _sem_type, calculate_ops_bbox,
    _get_comp_ref, _remove_overlaps, _prepare_components,
    BBOX_PAD, GRID_SIZE,
    TIER_GAP, COMP_V_GAP, SAT_H_GAP, SAT_V_GAP,
    MAX_COMPS_PER_COLUMN,
)


def _build_parent_map(components: list, netlist: list) -> dict[str, str]:
    scores: dict[tuple[str, str], int] = {}
    for conn in netlist:
        sr = conn["source"].split(":")[0]
        tr = conn["target"].split(":")[0]
        sc = _get_comp_ref(sr, components)
        tc = _get_comp_ref(tr, components)
        if not sc or not tc:
            continue
        for sat_c, ic_c in [(sc, tc), (tc, sc)]:
            if sat_c.get("tier", -1) == -1 and ic_c.get("tier", -1) >= 0:
                key = (sat_c["ref_des"], ic_c["ref_des"])
                scores[key] = scores.get(key, 0) + 1

    parent: dict[str, str] = {}
    for (sat, ic), _ in sorted(scores.items(), key=lambda kv: -kv[1]):
        if sat not in parent:
            parent[sat] = ic
    return parent


class LegacyPlacer:
    """Tier-column placement engine."""

    def place(self, components: list[dict], netlist: list, pin_matrix: dict) -> list:
        components = _prepare_components(components)
        netlist = netlist or []
        pin_matrix = pin_matrix or {}

        parent_map = _build_parent_map(components, netlist)

        for c in components:
            if c.get("tier", -1) != -1:
                continue
            if c["ref_des"] in parent_map:
                continue
            fc = c.get("for_component", "")
            if fc and _get_comp_ref(fc, components):
                parent_map[c["ref_des"]] = fc

        tiers: dict[int, list] = {0: [], 1: [], 2: [], 3: []}
        sats: list[dict] = []
        for c in components:
            if c.get("tier", -1) == -1:
                sats.append(c)
            else:
                tiers.setdefault(c["tier"], []).append(c)

        for t in tiers:
            tiers[t].sort(key=lambda c: -c.get("height", 0))

        x_cursor = 0.0

        for tier_idx in sorted(tiers):
            comps = tiers[tier_idx]
            if not comps:
                continue
            tier_w = max(c.get("width", 0) for c in comps) + BBOX_PAD * 2
            col_count = max(1, math.ceil(len(comps) / MAX_COMPS_PER_COLUMN))
            col_w = tier_w + TIER_GAP
            cols = [[] for _ in range(col_count)]
            for i, c in enumerate(comps):
                col_idx = i // MAX_COMPS_PER_COLUMN
                cols[col_idx].append(c)

            for col_idx, col_comps in enumerate(cols):
                y_cursor = 0.0
                for c in col_comps:
                    c["x"] = _snap(x_cursor + col_idx * col_w +
                                   (tier_w - c.get("width", 0)) / 2)
                    c["y"] = _snap(y_cursor - c["bbox"]["y"])
                    y_cursor += c.get("height", 0) + BBOX_PAD * 2 + COMP_V_GAP

            x_cursor += col_count * col_w + TIER_GAP

        # Satellites
        sat_groups: dict[str, list] = {}
        orphan_sats: list[dict] = []
        for s in sats:
            par = parent_map.get(s["ref_des"])
            par_c = _get_comp_ref(par, components) if par else None
            if par_c and par_c.get("tier", -1) >= 0:
                sat_groups.setdefault(par, []).append(s)
            else:
                orphan_sats.append(s)

        for par_ref, group in sat_groups.items():
            par_c = _get_comp_ref(par_ref, components)
            if not par_c:
                continue
            sy_start = _snap(par_c["y"])
            right_group = [s for i, s in enumerate(group) if i % 2 == 0]
            left_group = [s for i, s in enumerate(group) if i % 2 == 1]

            if right_group:
                sx = _snap(par_c["x"] + par_c.get("width", 0) + SAT_H_GAP)
                sat_col_count = max(1, math.ceil(len(right_group) / MAX_COMPS_PER_COLUMN))
                sat_cols = [[] for _ in range(sat_col_count)]
                for i, s in enumerate(right_group):
                    col_idx = i // MAX_COMPS_PER_COLUMN
                    sat_cols[col_idx].append(s)
                for col_idx, col_sats in enumerate(sat_cols):
                    y_cursor = sy_start
                    for s in col_sats:
                        s["x"] = _snap(sx + col_idx * (s.get("width", 0) + SAT_H_GAP))
                        s["y"] = _snap(y_cursor)
                        y_cursor += s.get("height", 0) + SAT_V_GAP

            if left_group:
                sat_col_count = max(1, math.ceil(len(left_group) / MAX_COMPS_PER_COLUMN))
                sat_cols = [[] for _ in range(sat_col_count)]
                for i, s in enumerate(left_group):
                    col_idx = i // MAX_COMPS_PER_COLUMN
                    sat_cols[col_idx].append(s)
                for col_idx, col_sats in enumerate(sat_cols):
                    y_cursor = sy_start
                    for s in col_sats:
                        s["x"] = _snap(par_c["x"] - SAT_H_GAP -
                                       col_idx * (s.get("width", 0) + SAT_H_GAP) - s.get("width", 0))
                        s["y"] = _snap(y_cursor)
                        y_cursor += s.get("height", 0) + SAT_V_GAP

        # Orphan satellites
        if orphan_sats:
            rx = max((c["x"] + c.get("width", 0) for c in components
                      if c.get("tier", -1) != -1), default=x_cursor)
            rx = _snap(rx + TIER_GAP)
            col_w_orphan = max(s.get("width", 0) for s in orphan_sats) + SAT_H_GAP
            orphan_col_count = max(1, math.ceil(len(orphan_sats) / MAX_COMPS_PER_COLUMN))
            orphan_cols = [[] for _ in range(orphan_col_count)]
            for i, s in enumerate(orphan_sats):
                col_idx = i // MAX_COMPS_PER_COLUMN
                orphan_cols[col_idx].append(s)
            for col_idx, col_sats in enumerate(orphan_cols):
                y_cursor = 0.0
                for s in col_sats:
                    s["x"] = _snap(rx + col_idx * col_w_orphan)
                    s["y"] = _snap(y_cursor)
                    y_cursor += s.get("height", 0) + SAT_V_GAP

        # Centre
        xs = [c["x"] for c in components]
        ys = [c["y"] for c in components]
        if xs:
            ox = _snap((max(xs) + min(xs)) / 2)
            oy = _snap((max(ys) + min(ys)) / 2)
            for c in components:
                c["x"] = _snap(c["x"] - ox)
                c["y"] = _snap(c["y"] - oy)

        _remove_overlaps(components)

        for c in components:
            bbox = c.get("bbox", {})
            c["x"] = _snap(c["x"] - bbox.get("x", 0))
            c["y"] = _snap(c["y"] - bbox.get("y", 0))

        return [{"ref_des": c["ref_des"], "x": c["x"], "y": c["y"],
                 "rotation": c.get("rotation", 0.0)} for c in components]
