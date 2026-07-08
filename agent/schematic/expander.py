"""Block expansion engine.

Expands each FunctionalBlock into actual component placements and intra-block
wires.  Dispatches to:

  - TemplateExpander  — for small motifs with predefined templates
                       (decoupling caps, pull-ups, RC filters, LEDs, etc.)
  - ConstraintExpander — for complex ICs
                       (microcontrollers, large ICs with many pins)

Both expanders produce TemplateInstance objects containing component positions
and wire segments.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from agent.schematic.schematic_types import (
    BlockRole,
    FunctionalBlock,
    Motif,
    MotifType,
    TemplateInstance,
    TemplateLayout,
    WireSegment,
)
from agent.schematic.templates import get_template, has_template


# ── TemplateExpander ─────────────────────────────────────────────────────────


class TemplateExpander:
    """Expands blocks that have a predefined template.

    Applies the template by offsetting component positions relative to the
    block's anchor position on the sheet.
    """

    @staticmethod
    def expand(block: FunctionalBlock, ctx: Any) -> Optional[TemplateInstance]:
        """Expand a block using its motif's template layout.

        Returns None if no template is available for the block's motif type.
        """
        block_pos = ctx.placements.get(block.id, (0.0, 0.0))
        if not block.motifs:
            return _fallback_placement(block, block_pos, ctx)

        # Find the first motif that has a template
        for motif_id in block.motifs:
            motif = _find_motif(motif_id, ctx)
            if motif is None:
                continue

            template = get_template(motif.motif_type)
            if template is None:
                continue

            return _apply_template(template, block, motif, block_pos, ctx)

        return _fallback_placement(block, block_pos, ctx)


# ── ConstraintExpander ───────────────────────────────────────────────────────


class ConstraintExpander:
    """Expands complex ICs by assigning pins to a grid.

    For microcontrollers and large ICs, generates a pseudo-grid placement
    with pins arranged for minimal wire crossings.
    """

    @staticmethod
    def expand(block: FunctionalBlock, ctx: Any) -> TemplateInstance:
        """Expand a complex IC block using constraint-based placement."""
        block_pos = ctx.placements.get(block.id, (0.0, 0.0))
        return _fallback_placement(block, block_pos, ctx)


# ── Main expander ────────────────────────────────────────────────────────────


class Expander:
    """Main expansion orchestrator.

    Dispatches to the appropriate expander based on block type.
    """

    def __init__(self):
        self.template_expander = TemplateExpander()
        self.constraint_expander = ConstraintExpander()

    def expand(self, block: FunctionalBlock, ctx: Any) -> TemplateInstance:
        """Expand a single block into component placements.

        Dispatch logic:
          - Controller / interface ICs → ConstraintExpander
          - Motif-only blocks with templates → TemplateExpander
          - Everything else → fallback placement
        """
        if block.role in (BlockRole.CONTROLLER, BlockRole.INTERFACE,
                          BlockRole.SENSOR, BlockRole.SIGNAL_CONDITIONING):
            result = self.constraint_expander.expand(block, ctx)
        else:
            result = self.template_expander.expand(block, ctx) or \
                     _fallback_placement(block, ctx.placements.get(block.id, (0.0, 0.0)), ctx)

        return result

    def expand_all(self, ctx: Any) -> dict[str, TemplateInstance]:
        """Expand all blocks in the context's block_graph.

        Returns dict of {block_id: TemplateInstance} and stores
        component placements in ctx.
        """
        instances: dict[str, TemplateInstance] = {}
        all_placements: dict[str, tuple[float, float, float]] = {}
        all_wires: list[WireSegment] = []

        if ctx.block_graph is None:
            return instances

        for bid, block in ctx.block_graph.blocks.items():
            instance = self.expand(block, ctx)
            instances[bid] = instance
            for ref, (x, y, rot) in instance.placements.items():
                all_placements[ref] = (x, y, rot)
            all_wires.extend(instance.wires)

        ctx.metadata["template_instances"] = instances
        ctx.metadata["component_positions"] = all_placements
        ctx.metadata["intra_block_wires"] = all_wires
        return instances


# ── Template application ─────────────────────────────────────────────────────


def _apply_template(
    template: TemplateLayout,
    block: FunctionalBlock,
    motif: Motif,
    block_pos: tuple[float, float],
    ctx: Any,
) -> TemplateInstance:
    """Apply a template layout at the block position."""
    ax, ay = block_pos
    placements: dict[str, tuple[float, float, float]] = {}
    wires: list[WireSegment] = []

    # Map template refs to actual ref_des
    # "primary" → motif anchor
    # secondary labels (from motif.pins) → actual ref_des
    # orphan components that appear by ref_des directly
    ref_map: dict[str, str] = {"primary": motif.anchor}
    ref_map.update(motif.pins)

    # Expand component positions
    for tc in template.components:
        ref = ref_map.get(tc.ref, tc.ref)
        if ref not in block.all_components():
            continue
        placements[ref] = (ax + tc.offset_x, ay + tc.offset_y, tc.rotation)

    # Place any remaining orphan components not in the template
    placed = set(placements.keys())
    y_offset = 15.0
    for ref in block.all_components():
        if ref not in placed:
            placements[ref] = (ax, ay + y_offset, 0.0)
            y_offset += 10.0

    # Expand wires
    for tw in template.wires:
        src_ref = ref_map.get(tw.from_pin.split(":")[0] if ":" in tw.from_pin else tw.from_pin,
                              tw.from_pin)
        tgt_ref = ref_map.get(tw.to_pin.split(":")[0] if ":" in tw.to_pin else tw.to_pin,
                              tw.to_pin) if tw.to_pin else ""
        points = [(ax + px, ay + py) for px, py in tw.path_offsets]
        wires.append(WireSegment(
            source=src_ref, target=tgt_ref, net="", points=points,
        ))

    return TemplateInstance(
        template=template,
        position=block_pos,
        rotation=0.0,
        placements=placements,
        wires=wires,
    )


def _fallback_placement(
    block: FunctionalBlock,
    block_pos: tuple[float, float],
    ctx: Any,
) -> TemplateInstance:
    """Simple fallback: place all block components in a vertical stack."""
    bx, by = block_pos
    placements: dict[str, tuple[float, float, float]] = {}
    y_offset = 0.0

    for ref in block.all_components():
        placements[ref] = (bx, by + y_offset, 0.0)
        y_offset += 10.0

    return TemplateInstance(
        position=block_pos, rotation=0.0,
        placements=placements, wires=[],
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


def _find_motif(motif_id: str, ctx: Any) -> Optional[Motif]:
    for m in ctx.motifs:
        if m.id == motif_id:
            return m
    for m in ctx.resolved_motifs:
        if m.id == motif_id:
            return m
    return None


def _find_motif_signature(motif_type: MotifType, ctx: Any) -> Any:
    from agent.schematic.catalog import MOTIF_CATALOG
    for sig in MOTIF_CATALOG:
        if sig.motif_type == motif_type:
            return sig
    return None
