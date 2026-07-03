"""Spring-graph placement (graph mode).

Simple spring-layout placement: run nx.spring_layout on the full
weighted connectivity graph and translate to physical coordinates.

Usage::

    from agent.placement.graph import GraphPlacer
    placer = GraphPlacer()
    placements = placer.place(components, netlist, pin_matrix)
"""

from __future__ import annotations

import math

import networkx as nx

from agent.placement.blocks_v2 import (
    _snap, _build_weighted_graph, calculate_ops_bbox,
    _get_comp_ref, _remove_overlaps, _prepare_components,
    GRID_SIZE, SMALL_CIRCUIT_MAX_COMPONENTS,
)


class GraphPlacer:
    """Simple spring-graph placement engine."""

    def __init__(self, spring_k: float = 2.0, spring_iters: int = 100, seed: int = 42):
        self._spring_k = spring_k
        self._spring_iters = spring_iters
        self._seed = seed

    def place(self, components: list[dict], netlist: list, pin_matrix: dict) -> list:
        components = _prepare_components(components)
        netlist = netlist or []
        pin_matrix = pin_matrix or {}

        graph = _build_weighted_graph(components, netlist, pin_matrix)

        if graph.number_of_nodes() < 2:
            placements = []
            for c in components:
                c["x"] = 0.0
                c["y"] = 0.0
                placements.append({
                    "ref_des": c["ref_des"],
                    "x": 0.0,
                    "y": 0.0,
                    "rotation": c.get("rotation", 0.0),
                })
            return placements

        small = graph.number_of_nodes() <= SMALL_CIRCUIT_MAX_COMPONENTS
        k_val = self._spring_k if small else self._spring_k * 0.5
        iters = self._spring_iters if small else 50

        pos = nx.spring_layout(
            graph, weight="weight",
            k=k_val, iterations=iters,
            seed=self._seed,
        )

        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        rng_x = max(max_x - min_x, 1.0)
        rng_y = max(max_y - min_y, 1.0)

        scale = 200.0 if small else 300.0

        for c in components:
            if c["ref_des"] not in pos:
                c["x"] = 0.0
                c["y"] = 0.0
                continue
            lx, ly = pos[c["ref_des"]]
            c["x"] = _snap((lx - min_x) / rng_x * scale)
            c["y"] = _snap((ly - min_y) / rng_y * scale)

        _remove_overlaps(components)

        for c in components:
            bbox = c.get("bbox", {})
            c["x"] = _snap(c["x"] - bbox.get("x", 0))
            c["y"] = _snap(c["y"] - bbox.get("y", 0))

        return [{"ref_des": c["ref_des"], "x": c["x"], "y": c["y"],
                 "rotation": c.get("rotation", 0.0)} for c in components]
