"""Constraint-based block placement.

Places blocks on the schematic sheet using:
  - Topological ordering (power → controller → interfaces)
  - Semantic roles (power at top, controller center, interfaces edges, sensors bottom)
  - Layout rules as constraints

Produces deterministic placement given the same BlockGraph.
"""

from __future__ import annotations

from typing import Any, Optional

from agent.schematic.schematic_types import (
    BlockRole,
    LayoutConstraint,
    Predicate,
)


# ── Layout parameters ───────────────────────────────────────────────────────


BLOCK_SPACING_X = 50.0   # mm between blocks horizontally
BLOCK_SPACING_Y = 40.0   # mm between blocks vertically
BLOCK_MARGIN = 20.0      # margin from sheet edge
BLOCK_MIN_WIDTH = 30.0   # minimum block width estimate
BLOCK_MIN_HEIGHT = 25.0  # minimum block height estimate


# ── Role ordering (for layer assignment) ────────────────────────────────────


_ROLE_LAYER: dict[BlockRole, int] = {
    BlockRole.POWER_SOURCE: 0,
    BlockRole.POWER_CONDITIONING: 1,
    BlockRole.CONTROLLER: 2,
    BlockRole.INTERFACE: 2,
    BlockRole.SENSOR: 3,
    BlockRole.SIGNAL_CONDITIONING: 3,
    BlockRole.ACTUATOR: 4,
    BlockRole.PASSIVE_NETWORK: 5,
    BlockRole.UNKNOWN: 6,
}


# ── Constraint generation ───────────────────────────────────────────────────


def generate_constraints(ctx: Any) -> list[LayoutConstraint]:
    """Generate placement constraints from layout rules and block graph."""
    constraints: list[LayoutConstraint] = []
    bg = ctx.block_graph
    if bg is None:
        return constraints

    blocks = list(bg.blocks.values())

    # Constraint 1: Power blocks above controller
    power_ids = {b.id for b in blocks if b.role in (
        BlockRole.POWER_SOURCE, BlockRole.POWER_CONDITIONING)}
    ctrl_ids = {b.id for b in blocks if b.role == BlockRole.CONTROLLER}
    for pid in power_ids:
        for cid in ctrl_ids:
            constraints.append(LayoutConstraint(
                subject_id=pid, predicate=Predicate.ABOVE, object_id=cid, weight=2.0,
            ))

    # Constraint 2: Sensors below controller
    sensor_ids = {b.id for b in blocks if b.role in (
        BlockRole.SENSOR, BlockRole.ACTUATOR)}
    for sid in sensor_ids:
        for cid in ctrl_ids:
            constraints.append(LayoutConstraint(
                subject_id=sid, predicate=Predicate.BELOW, object_id=cid, weight=1.5,
            ))

    # Constraint 3: Interfaces to the sides of controller
    iface_ids = {b.id for b in blocks if b.role in (
        BlockRole.INTERFACE, BlockRole.SIGNAL_CONDITIONING)}
    iface_list = list(iface_ids)
    for i, iid in enumerate(iface_list):
        for cid in ctrl_ids:
            if i % 2 == 0:
                constraints.append(LayoutConstraint(
                    subject_id=iid, predicate=Predicate.LEFT_OF, object_id=cid, weight=1.0,
                ))
            else:
                constraints.append(LayoutConstraint(
                    subject_id=iid, predicate=Predicate.RIGHT_OF, object_id=cid, weight=1.0,
                ))

    # Constraint 4: Connected blocks adjacent
    for edge in bg.edges:
        constraints.append(LayoutConstraint(
            subject_id=edge.source_id, predicate=Predicate.ADJACENT_TO,
            object_id=edge.target_id, weight=0.5,
        ))

    return constraints


# ── Block sizing estimate ────────────────────────────────────────────────────


def _estimate_block_size(block: Any, ctx: Any) -> tuple[float, float]:
    """Estimate width and height of a block based on component count."""
    graph = ctx.synthesis_graph
    total_pins = 0
    comp_count = len(block.orphan_components)

    for ref in block.orphan_components:
        comp = graph.components.get(ref)
        if comp:
            total_pins += len(comp.pins)

    width = max(BLOCK_MIN_WIDTH, comp_count * 15.0, total_pins * 2.0)
    height = max(BLOCK_MIN_HEIGHT, comp_count * 10.0)
    return (width, height)


# ── Grid placement solver ────────────────────────────────────────────────────


def place_blocks(ctx: Any) -> dict[str, tuple[float, float]]:
    """Place all blocks on the sheet using constraint-based layout.

    Returns dict of {block_id: (x, y)} and stores it in ctx.placements.
    """
    bg = ctx.block_graph
    if bg is None or not bg.blocks:
        ctx.placements = {}
        return ctx.placements

    blocks = list(bg.blocks.values())

    # 1. Group blocks by layer
    layers: dict[int, list[Any]] = {}
    for block in blocks:
        layer = _ROLE_LAYER.get(block.role, 6)
        if layer not in layers:
            layers[layer] = []
        layers[layer].append(block)

    # 2. Sort layers
    sorted_layers = sorted(layers.items())

    # 3. Place by layer, then by connectivity
    placements: dict[str, tuple[float, float]] = {}
    y_cursor = BLOCK_MARGIN

    for layer_idx, layer_blocks in sorted_layers:
        # Sort blocks within layer: most connected first
        block_scores: dict[str, int] = {}
        for edge in bg.edges:
            for b in layer_blocks:
                if edge.source_id == b.id or edge.target_id == b.id:
                    block_scores[b.id] = block_scores.get(b.id, 0) + 1

        layer_blocks_sorted = sorted(
            layer_blocks,
            key=lambda b: (-block_scores.get(b.id, 0), b.name),
        )

        x_cursor = BLOCK_MARGIN
        max_height_in_layer = 0.0

        for block in layer_blocks_sorted:
            w, h = _estimate_block_size(block, ctx)
            placements[block.id] = (x_cursor, y_cursor)
            x_cursor += w + BLOCK_SPACING_X
            max_height_in_layer = max(max_height_in_layer, h)

        y_cursor += max_height_in_layer + BLOCK_SPACING_Y

    ctx.placements = placements
    return placements
