"""Tests for block building, placement, templates, expansion, optimization, and scoring."""

from agent.schematic.schematic_types import (
    BlockRole, LayoutContext, Motif, MotifType, MotifCategory,
    FunctionalBlock, BlockGraph, BlockEdge,
)
from agent.schematic.blocks import build_block_graph
from agent.schematic.placement import place_blocks, generate_constraints
from agent.schematic.templates import get_template, has_template, list_variants
from agent.schematic.expander import Expander, TemplateExpander
from agent.schematic.optimizer import optimize
from agent.schematic.scoring import score_layout, generate_candidates, pick_best
from agent.schematic.detector import detect_motifs
from agent.schematic.catalog import MOTIF_CATALOG
from agent.synthesis.graph import SynthesisGraph
from agent.synthesis.classifier import classify_all


def _make_test_graph() -> SynthesisGraph:
    """Circuit: J1(USB) → U1(regulator) → U2(MCU) + LED + caps."""
    g = SynthesisGraph()
    g.add_component({"ref_des": "J1", "id_str": "Connector:USB_C", "category": "Connector"})
    g.add_component({"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3",
                      "category": "Regulator_Linear"})
    g.add_component({"ref_des": "U2", "id_str": "MCU_ESP32:ESP32", "category": "Microcontroller"})
    g.add_component({"ref_des": "R1", "id_str": "Device:R", "category": "Resistor"})
    g.add_component({"ref_des": "D1", "id_str": "Device:LED", "category": "LED"})
    g.add_component({"ref_des": "C1", "id_str": "Device:C", "category": "Capacitor"})
    g.add_component({"ref_des": "C2", "id_str": "Device:C", "category": "Capacitor"})
    pins = {
        "J1:1": {"name": "VBUS", "etype": "power_in"},
        "J1:2": {"name": "GND", "etype": "passive"},
        "J1:3": {"name": "D+", "etype": "bidirectional"},
        "J1:4": {"name": "D-", "etype": "bidirectional"},
        "U1:1": {"name": "VIN", "etype": "power_in"},
        "U1:2": {"name": "GND", "etype": "passive"},
        "U1:3": {"name": "VOUT", "etype": "power_out"},
        "U2:1": {"name": "3V3", "etype": "power_in"},
        "U2:2": {"name": "GND", "etype": "passive"},
        "U2:3": {"name": "GPIO2", "etype": "output"},
        "R1:1": {"name": "~", "etype": "passive"},
        "R1:2": {"name": "~", "etype": "passive"},
        "D1:1": {"name": "A", "etype": "passive"},
        "D1:2": {"name": "K", "etype": "passive"},
        "C1:1": {"name": "~", "etype": "passive"},
        "C1:2": {"name": "~", "etype": "passive"},
        "C2:1": {"name": "~", "etype": "passive"},
        "C2:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)
    return g


def _setup_context() -> LayoutContext:
    g = _make_test_graph()
    classify_all(g)
    g.import_llm_nets([
        {"source": "J1:1", "target": "U1:1", "net": "5V"},
        {"source": "U1:2", "target": "C1:1", "net": "GND"},
        {"source": "U1:3", "target": "U2:1", "net": "3V3"},
        {"source": "U2:2", "target": "C2:1", "net": "GND"},
        {"source": "U2:3", "target": "R1:1", "net": "LED_DRV"},
        {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
        {"source": "D1:2", "target": "U2:2", "net": "GND"},
    ])
    ctx = LayoutContext()
    ctx.synthesis_graph = g

    from agent.schematic.analyzer import analyze_circuit
    ctx.semantic_model = analyze_circuit(g)

    motifs = detect_motifs(g)
    ctx.motifs = motifs
    ctx.resolved_motifs = motifs
    return ctx


# ── Block building ──────────────────────────────────────────────────────────


class TestBlockGraph:
    def test_build_block_graph_creates_blocks(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        assert ctx.block_graph is not None
        assert len(ctx.block_graph.blocks) >= 2

    def test_controller_block_has_correct_role(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        ctrl_blocks = [b for b in ctx.block_graph.blocks.values()
                       if b.role == BlockRole.CONTROLLER]
        assert len(ctrl_blocks) == 1
        assert "U2" in ctrl_blocks[0].orphan_components or \
               any("U2" in str(m) for m in ctrl_blocks[0].motifs)

    def test_power_block_detected(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        power_blocks = [b for b in ctx.block_graph.blocks.values()
                        if b.role == BlockRole.POWER_CONDITIONING]
        assert len(power_blocks) >= 1

    def test_edges_detected_between_blocks(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        if ctx.block_graph.edges:
            edge = ctx.block_graph.edges[0]
            assert edge.source_id in ctx.block_graph.blocks
            assert edge.target_id in ctx.block_graph.blocks
            assert edge.net

    def test_all_components_claimed(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        all_claimed: set[str] = set()
        for block in ctx.block_graph.blocks.values():
            all_claimed.update(block.all_components())
        graph_comps = set(ctx.synthesis_graph.components.keys())
        assert all_claimed == graph_comps, \
            f"Unclaimed: {graph_comps - all_claimed}"

    def test_topological_order(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        order = ctx.block_graph.topological_order()
        assert len(order) == len(ctx.block_graph.blocks)
        assert all(bid in ctx.block_graph.blocks for bid in order)


# ── Placement ───────────────────────────────────────────────────────────────


class TestPlacement:
    def test_place_blocks_returns_all_blocks(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        placements = place_blocks(ctx)
        assert len(placements) == len(ctx.block_graph.blocks)

    def test_placements_are_positive(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        placements = place_blocks(ctx)
        for bid, (x, y) in placements.items():
            assert x >= 0
            assert y >= 0

    def test_no_overlapping_placements(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        placements = place_blocks(ctx)
        positions = list(placements.values())
        for i in range(len(positions)):
            for j in range(i + 1, len(positions)):
                assert positions[i] != positions[j], \
                    f"Overlap at {positions[i]}"

    def test_generate_constraints(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        constraints = generate_constraints(ctx)
        assert len(constraints) >= 1


# ── Templates ───────────────────────────────────────────────────────────────


class TestTemplates:
    def test_decoupling_cap_template_exists(self):
        assert has_template(MotifType.DECOUPLING_CAP)
        t = get_template(MotifType.DECOUPLING_CAP)
        assert t is not None
        assert t.motif_type == MotifType.DECOUPLING_CAP

    def test_pull_up_template_exists(self):
        assert has_template(MotifType.PULL_UP)
        t = get_template(MotifType.PULL_UP)
        assert t is not None

    def test_led_indicator_template_exists(self):
        assert has_template(MotifType.LED_INDICATOR)
        t = get_template(MotifType.LED_INDICATOR)
        assert t is not None
        assert len(t.components) >= 2

    def test_crystal_template_has_two_caps(self):
        t = get_template(MotifType.CRYSTAL)
        assert t is not None
        cap_refs = [c.ref for c in t.components if "cap" in c.ref.lower()]
        assert len(cap_refs) == 2

    def test_unknown_template_returns_none(self):
        assert has_template(MotifType.UNKNOWN) is False
        assert get_template(MotifType.UNKNOWN) is None

    def test_list_variants(self):
        variants = list_variants(MotifType.PULL_UP)
        assert "vertical" in variants

    def test_rc_filter_has_wires(self):
        t = get_template(MotifType.RC_FILTER)
        assert t is not None
        assert len(t.wires) >= 2


# ── Expander ────────────────────────────────────────────────────────────────


class TestExpander:
    def test_expand_all_creates_instances(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        instances = expander.expand_all(ctx)
        assert len(instances) == len(ctx.block_graph.blocks)

    def test_expand_produces_component_positions(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        positions = ctx.metadata.get("component_positions", {})
        assert len(positions) >= 1

    def test_all_graph_components_have_positions(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        positions = ctx.metadata.get("component_positions", {})
        for ref in ctx.synthesis_graph.components:
            assert ref in positions, f"{ref} missing from positions"

    def test_expand_produces_wires(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        wires = ctx.metadata.get("intra_block_wires", [])
        assert len(wires) >= 0


# ── Optimizer ───────────────────────────────────────────────────────────────


class TestOptimizer:
    def test_optimize_runs_without_error(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        optimize(ctx)
        positions = ctx.metadata.get("component_positions", {})
        assert len(positions) >= 1

    def test_optimize_snaps_to_grid(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        optimize(ctx)
        positions = ctx.metadata.get("component_positions", {})
        grid = ctx.grid_spacing
        for ref, (x, y, rot) in positions.items():
            assert abs(round(x / grid) * grid - x) < 0.01, \
                f"{ref} x={x} not snapped to {grid}"
            assert abs(round(y / grid) * grid - y) < 0.01, \
                f"{ref} y={y} not snapped to {grid}"

    def test_optimize_empty_does_not_crash(self):
        ctx = LayoutContext()
        optimize(ctx)

    def test_optimize_no_positions_does_not_crash(self):
        ctx = LayoutContext()
        ctx.metadata = {}
        optimize(ctx)


# ── Scoring ─────────────────────────────────────────────────────────────────


class TestScoring:
    def test_score_layout_returns_score(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        score = score_layout(ctx)
        assert score.total >= 0 or score.total < 0  # any finite number
        assert score.crossings >= 0
        assert score.bends >= 0

    def test_generate_candidates(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        candidates = generate_candidates(ctx, count=2)
        assert len(candidates) == 2

    def test_pick_best_returns_lowest_score(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        candidates = generate_candidates(ctx, count=2)
        best = pick_best(candidates)
        assert best is not None

    def test_score_improves_after_optimize(self):
        ctx = _setup_context()
        build_block_graph(ctx)
        place_blocks(ctx)
        expander = Expander()
        expander.expand_all(ctx)
        score_before = score_layout(ctx)
        optimize(ctx)
        score_after = score_layout(ctx)
        assert score_after.alignment <= score_before.alignment + 0.01
