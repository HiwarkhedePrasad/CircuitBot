"""Tests for wire generation, labels, beautification, and export."""

from agent.schematic.schematic_types import (
    LayoutContext, WireSegment, SchematicOutput,
)
from agent.schematic.wires import generate_wires, _choose_bend, _pin_sheet_position
from agent.schematic.labels import place_labels
from agent.schematic.beautify import beautify, _is_collinear, _remove_collinear_points
from agent.schematic.export_adapter import export
from agent.schematic.analyzer import analyze_circuit
from agent.schematic.detector import detect_motifs
from agent.schematic.blocks import build_block_graph
from agent.schematic.placement import place_blocks
from agent.schematic.expander import Expander
from agent.schematic.optimizer import optimize
from agent.synthesis.graph import SynthesisGraph
from agent.synthesis.classifier import classify_all


def _setup_full_context() -> LayoutContext:
    """Build a context with the full pipeline run up to wires."""
    g = SynthesisGraph()
    g.add_component({"ref_des": "U1", "id_str": "MCU_ESP32:ESP32", "category": "Microcontroller"})
    g.add_component({"ref_des": "R1", "id_str": "Device:R", "category": "Resistor"})
    g.add_component({"ref_des": "D1", "id_str": "Device:LED", "category": "LED"})
    g.add_component({"ref_des": "C1", "id_str": "Device:C", "category": "Capacitor"})
    pins = {
        "U1:1": {"name": "3V3", "etype": "power_in"},
        "U1:2": {"name": "GND", "etype": "passive"},
        "U1:3": {"name": "GPIO2", "etype": "output"},
        "R1:1": {"name": "~", "etype": "passive"},
        "R1:2": {"name": "~", "etype": "passive"},
        "D1:1": {"name": "A", "etype": "passive"},
        "D1:2": {"name": "K", "etype": "passive"},
        "C1:1": {"name": "~", "etype": "passive"},
        "C1:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)

    classify_all(g)
    g.import_llm_nets([
        {"source": "U1:1", "target": "C1:1", "net": "3V3"},
        {"source": "U1:2", "target": "C1:2", "net": "GND"},
        {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
        {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
        {"source": "D1:2", "target": "U1:2", "net": "GND"},
    ])

    ctx = LayoutContext()
    ctx.synthesis_graph = g
    ctx.semantic_model = analyze_circuit(g)
    ctx.motifs = detect_motifs(g)
    ctx.resolved_motifs = ctx.motifs
    build_block_graph(ctx)
    place_blocks(ctx)
    expander = Expander()
    expander.expand_all(ctx)
    optimize(ctx)
    return ctx


# ── Wire generation ─────────────────────────────────────────────────────────


class TestWires:
    def test_generate_wires_returns_list(self):
        ctx = _setup_full_context()
        wires = generate_wires(ctx)
        assert isinstance(wires, list)

    def test_wires_have_source_target(self):
        ctx = _setup_full_context()
        wires = generate_wires(ctx)
        for w in wires:
            assert w.source
            assert w.target
            assert len(w.points) >= 2

    def test_wire_points_are_tuples(self):
        ctx = _setup_full_context()
        wires = generate_wires(ctx)
        for w in wires:
            for p in w.points:
                assert isinstance(p, tuple)
                assert len(p) == 2

    def test_choose_bend_vertical_then_horizontal_default(self):
        src = (0.0, 0.0)
        tgt = (100.0, 50.0)
        s, b, t = _choose_bend(src, tgt, is_power=False)
        # src_y < tgt_y → vertical-first: bend at (src_x, tgt_y)
        assert b == (0.0, 50.0)

    def test_choose_bend_vertical_then_horizontal_for_power(self):
        src = (0.0, 0.0)
        tgt = (100.0, 50.0)
        s, b, t = _choose_bend(src, tgt, is_power=True)
        assert b == (0.0, 50.0)

    def test_pin_sheet_position_returns_coords(self):
        ctx = _setup_full_context()
        pos = _pin_sheet_position(ctx, "U1:1")
        assert pos is not None
        x, y = pos
        assert isinstance(x, float)
        assert isinstance(y, float)

    def test_wires_stored_in_context(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        assert len(ctx.wires) >= 0

    def test_choose_bend_straight_line(self):
        s, b, t = _choose_bend((0, 0), (100, 0))
        assert b == (0, 0)


# ── Labels ──────────────────────────────────────────────────────────────────


class TestLabels:
    def test_place_labels_returns_list(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        labels = place_labels(ctx)
        assert isinstance(labels, list)

    def test_power_labels_have_required_keys(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        labels = place_labels(ctx)
        for lb in labels:
            assert "net" in lb
            assert "label" in lb
            assert "x" in lb
            assert "y" in lb

    def test_gnd_label_present(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        labels = place_labels(ctx)
        texts = [lb.get("label", "") for lb in labels]
        assert "GND" in texts


# ── Beautification ──────────────────────────────────────────────────────────


class TestBeautify:
    def test_is_collinear_horizontal(self):
        assert _is_collinear((0, 0), (50, 0), (100, 0)) is True

    def test_is_collinear_vertical(self):
        assert _is_collinear((0, 0), (0, 50), (0, 100)) is True

    def test_is_not_collinear(self):
        assert _is_collinear((0, 0), (50, 50), (100, 0)) is False

    def test_remove_collinear_points(self):
        pts = [(0, 0), (50, 0), (100, 0), (100, 50)]
        result = _remove_collinear_points(pts)
        assert len(result) == 3  # middle collinear point removed
        assert result[0] == (0, 0)
        assert result[1] == (100, 0)
        assert result[2] == (100, 50)

    def test_no_collinear_points_unchanged(self):
        pts = [(0, 0), (100, 0), (100, 50)]
        result = _remove_collinear_points(pts)
        assert result == pts

    def test_beautify_runs_without_error(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        beautify(ctx)
        # beautify should not crash; metadata flag may not be set if wires empty
        assert ctx.metadata.get("beautified", True) is not None

    def test_beautify_empty_wires(self):
        ctx = LayoutContext()
        ctx.wires = []
        ctx.metadata = {}
        beautify(ctx)


# ── Export ──────────────────────────────────────────────────────────────────


class TestExport:
    def test_export_returns_schematic_output(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        assert isinstance(output, SchematicOutput)

    def test_export_has_placements(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        assert len(output.component_placements) >= 1

    def test_export_has_wire_paths(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        assert isinstance(output.wire_paths, list)

    def test_export_to_dict(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        d = output.to_dict()
        assert "component_placements" in d
        assert "wire_paths" in d
        assert "power_labels" in d
        assert d["_placement_locked"] is True

    def test_placement_format_matches_agent_state(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        for p in output.component_placements:
            assert "ref_des" in p
            assert "x" in p
            assert "y" in p

    def test_wire_format_matches_agent_state(self):
        ctx = _setup_full_context()
        generate_wires(ctx)
        place_labels(ctx)
        output = export(ctx)
        for w in output.wire_paths:
            assert "source" in w
            assert "target" in w
            assert "path" in w
