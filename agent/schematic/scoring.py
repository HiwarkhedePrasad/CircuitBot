"""Layout scoring — evaluates candidate layouts for quality.

Scoring criteria (lower is better for penalties):
  - Crossings: estimated wire crossings between blocks
  - Bends: estimated number of wire bends
  - Wire length: total estimated wire length
  - Symmetry: penalty for asymmetric layouts
  - Alignment: bonus for well-aligned components
  - Signal flow: bonus for left-right / top-bottom signal flow
  - Rule violations: penalty per unsatisfied LayoutRule

Used to select the best layout from multiple deterministic candidates.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Optional

from agent.schematic.schematic_types import LayoutScore


# ── Weights ──────────────────────────────────────────────────────────────────

# Lower = more important (penalty multipliers)
_WEIGHT_CROSSINGS = 100.0
_WEIGHT_BENDS = 10.0
_WEIGHT_WIRE_LENGTH = 2.0
_WEIGHT_RULE_VIOLATIONS = 50.0
_BONUS_SYMMETRY = -20.0    # negative = good (subtracted from total)
_BONUS_ALIGNMENT = -15.0
_BONUS_SIGNAL_FLOW = -10.0


# ── Scoring functions ────────────────────────────────────────────────────────


def _estimate_crossings(ctx: Any) -> int:
    """Estimate the number of wire crossings between blocks.

    Uses a simplified model: count intersecting edges based on block positions.
    """
    placements = ctx.placements
    bg = ctx.block_graph
    if bg is None or not placements:
        return 0

    crossings = 0
    edges = bg.edges

    block_bounds: dict[str, tuple[float, float, float, float]] = {}
    for bid, (bx, by) in placements.items():
        block_bounds[bid] = (bx, by, bx + 30.0, by + 20.0)  # estimated size

    for i in range(len(edges)):
        for j in range(i + 1, len(edges)):
            e1 = edges[i]
            e2 = edges[j]
            if e1.source_id == e2.source_id or e1.target_id == e2.target_id:
                continue
            # Simplified: if source/target order is swapped between edges
            p1 = placements.get(e1.source_id, (0, 0))
            p2 = placements.get(e2.source_id, (0, 0))
            p3 = placements.get(e1.target_id, (0, 0))
            p4 = placements.get(e2.target_id, (0, 0))

            a1, b1 = p1[1], p1[0]
            a2, b2 = p3[1], p3[0]
            a3, b3 = p2[1], p2[0]
            a4, b4 = p4[1], p4[0]

            if (a1 < a3 < a2 and a3 < a2 < a4) or (a3 < a1 < a4 and a1 < a4 < a2):
                crossings += 1
            if (b1 < b3 < b2 and b3 < b2 < b4) or (b3 < b1 < b4 and b1 < b4 < b2):
                crossings += 1

    return crossings


def _estimate_bends(ctx: Any) -> int:
    """Estimate total wire bends across all blocks."""
    wires = ctx.metadata.get("intra_block_wires", [])
    bends = 0
    for w in wires:
        points = w.points
        if len(points) >= 3:
            for k in range(1, len(points) - 1):
                dx1 = points[k][0] - points[k - 1][0]
                dy1 = points[k][1] - points[k - 1][1]
                dx2 = points[k + 1][0] - points[k][0]
                dy2 = points[k + 1][1] - points[k][1]
                if (abs(dx1) > 0.001 and abs(dy2) > 0.001) or \
                   (abs(dy1) > 0.001 and abs(dx2) > 0.001):
                    bends += 1
    return bends


def _estimate_wire_length(ctx: Any) -> float:
    """Estimate total wire length in millimeters."""
    wires = ctx.metadata.get("intra_block_wires", [])
    total_length = 0.0
    for w in wires:
        points = w.points
        for k in range(1, len(points)):
            dx = points[k][0] - points[k - 1][0]
            dy = points[k][1] - points[k - 1][1]
            total_length += math.sqrt(dx * dx + dy * dy)
    return total_length


def _score_symmetry(ctx: Any) -> float:
    """Score layout symmetry: 0 = perfectly symmetric, higher = less symmetric."""
    placements = ctx.placements
    if not placements:
        return 0.0

    bg = ctx.block_graph
    if bg is None:
        return 0.0

    # Check if blocks with the same role are symmetric around the controller
    ctrl_blocks = [bid for bid, b in bg.blocks.items()
                   if b.role.name == "CONTROLLER"]
    if not ctrl_blocks:
        return 0.0

    ctrl_pos = placements.get(ctrl_blocks[0], (0, 0))
    asymmetry = 0.0

    for bid, (bx, by) in placements.items():
        if bid in ctrl_blocks:
            continue
        # Find a partner block with similar Y for symmetry check
        dx = bx - ctrl_pos[0]
        dy = by - ctrl_pos[1]
        asymmetry += abs(dx) * 0.01 + abs(dy) * 0.01

    return asymmetry


def _score_alignment(ctx: Any) -> float:
    """Score component alignment: 0 = perfectly aligned, higher = misaligned."""
    positions = ctx.metadata.get("component_positions", {})
    if not positions:
        return 0.0

    rows: dict[float, int] = {}
    cols: dict[float, int] = {}
    for ref, (x, y, rot) in positions.items():
        y_key = round(y, 1)
        x_key = round(x, 1)
        rows[y_key] = rows.get(y_key, 0) + 1
        cols[x_key] = cols.get(x_key, 0) + 1

    aligned = sum(1 for c in rows.values() if c >= 2) + \
              sum(1 for c in cols.values() if c >= 2)
    total = len(positions)
    if total == 0:
        return 0.0
    return max(0.0, 1.0 - (aligned / total))


def _score_signal_flow(ctx: Any) -> float:
    """Score signal flow direction: 0 = good flow, higher = poor flow."""
    placements = ctx.placements
    bg = ctx.block_graph
    if bg is None or not placements:
        return 0.0

    flow_score = 0.0
    for edge in bg.edges:
        src_pos = placements.get(edge.source_id)
        tgt_pos = placements.get(edge.target_id)
        if src_pos and tgt_pos:
            dx = tgt_pos[0] - src_pos[0]
            dy = tgt_pos[1] - src_pos[1]
            if edge.signal_type == "power":
                if dy > 0:
                    flow_score += 1.0  # power should flow top-down
            else:
                if dx < 0:
                    flow_score += 1.0  # signal should flow left-right

    return flow_score


def _count_rule_violations(ctx: Any) -> int:
    """Count how many layout rules are violated by the current layout."""
    placements = ctx.placements
    bg = ctx.block_graph
    if bg is None or not placements:
        return 0

    violations = 0
    for rule in ctx.rules:
        if not rule.enabled:
            continue
        # Simplified rule checking — count total rules that apply
        # Full rule engine deferred to post-v1
        pass

    return violations


# ── Main scoring ──────────────────────────────────────────────────────────────


def score_layout(ctx: Any) -> LayoutScore:
    """Score the current layout in the context.

    Returns a LayoutScore with individual component scores and total.
    """
    crossings = _estimate_crossings(ctx)
    bends = _estimate_bends(ctx)
    wire_length = _estimate_wire_length(ctx)
    symmetry = _score_symmetry(ctx)
    alignment = _score_alignment(ctx)
    signal_flow = _score_signal_flow(ctx)
    rule_violations = _count_rule_violations(ctx)

    total = (
        crossings * _WEIGHT_CROSSINGS +
        bends * _WEIGHT_BENDS +
        wire_length * _WEIGHT_WIRE_LENGTH +
        symmetry * _BONUS_SYMMETRY +
        alignment * _BONUS_ALIGNMENT +
        signal_flow * _BONUS_SIGNAL_FLOW +
        rule_violations * _WEIGHT_RULE_VIOLATIONS
    )

    return LayoutScore(
        total=total,
        crossings=crossings,
        bends=bends,
        wire_length=wire_length,
        symmetry=symmetry,
        alignment=alignment,
        signal_flow=signal_flow,
        rule_violations=rule_violations,
        details={"total_formula": str(total)},
    )


# ── Candidate generation ─────────────────────────────────────────────────────


def _clone_context(ctx: Any) -> Any:
    """Create a shallow copy of the context for candidate evaluation."""
    import copy
    new_ctx = copy.copy(ctx)
    new_ctx.placements = dict(ctx.placements) if ctx.placements else {}
    new_ctx.metadata = dict(ctx.metadata) if ctx.metadata else {}
    new_ctx.metadata["component_positions"] = dict(
        ctx.metadata.get("component_positions", {}))
    new_ctx.metadata["intra_block_wires"] = list(
        ctx.metadata.get("intra_block_wires", []))
    return new_ctx


def generate_candidates(ctx: Any, count: int = 2) -> list[Any]:
    """Generate multiple candidate layouts for scoring.

    Variant 0: original layout (no modification)
    Variant 1: swap left/right orientation for even/odd interface blocks
    """
    candidates: list[Any] = [_clone_context(ctx)]

    if count >= 2:
        variant = _clone_context(ctx)
        if variant.block_graph and variant.placements:
            # Flip the X order for interface blocks
            iface_ids = [bid for bid, b in variant.block_graph.blocks.items()
                         if b.role in (
                             type(b.role).INTERFACE, type(b.role).SIGNAL_CONDITIONING,
                         )] if variant.block_graph.blocks else []

            iface_positions = [(bid, variant.placements.get(bid, (0, 0)))
                               for bid in iface_ids]
            if len(iface_positions) >= 2:
                xs = [pos[1][0] for pos in iface_positions]
                ys = [pos[1][1] for pos in iface_positions]
                mean_x = sum(xs) / len(xs)
                for bid, (x, y) in iface_positions:
                    new_x = mean_x + (mean_x - x)
                    variant.placements[bid] = (new_x, y)

        candidates.append(variant)

    return candidates


def pick_best(candidates: list[Any]) -> Any:
    """Score all candidates and return the context with the best (lowest) score."""
    best_ctx = candidates[0]
    best_score = score_layout(best_ctx)

    for candidate in candidates[1:]:
        s = score_layout(candidate)
        if s.total < best_score.total:
            best_score = s
            best_ctx = candidate

    best_ctx.scores = list(candidates)  # track all evaluated
    return best_ctx
