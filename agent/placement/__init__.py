"""Placement engine package.

Usage::

    from agent.placement import PlacementEngine

    engine = PlacementEngine.create("blocks_v2")   # or "legacy", "graph"
    engine.place(components, netlist, pin_matrix)   # returns placements

    engine = PlacementEngine.create("sa_optimizer") # wraps blocks_v2 + SA
"""

from __future__ import annotations

PLACEMENT_ENGINE = "blocks_v2"  # "legacy" | "graph" | "blocks_v2" | "sa_optimizer"


class PlacementEngine:
    """Factory that dispatches to the appropriate placement backend."""

    def __init__(self, backend):
        self._backend = backend

    @staticmethod
    def create(name: str | None = None) -> PlacementEngine:
        name = name or PLACEMENT_ENGINE
        if name == "sa_optimizer":
            from agent.placement.sa_optimizer import SAOptimizer
            return PlacementEngine(SAOptimizer())
        if name == "legacy":
            from agent.placement.legacy import LegacyPlacer
            return PlacementEngine(LegacyPlacer())
        if name == "graph":
            from agent.placement.graph import GraphPlacer
            return PlacementEngine(GraphPlacer())
        from agent.placement.blocks_v2 import BlocksV2Placer
        return PlacementEngine(BlocksV2Placer())

    def place(self, components, netlist, pin_matrix) -> list:
        return self._backend.place(components, netlist, pin_matrix)
