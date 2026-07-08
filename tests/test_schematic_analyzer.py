"""Tests for the semantic analyzer and motif catalog."""

from agent.schematic.analyzer import analyze_circuit
from agent.schematic.catalog import MOTIF_CATALOG
from agent.schematic.schematic_types import (
    MotifType,
    MotifCategory,
    MotifSignature,
    PinNetConstraint,
    SecondarySpec,
)
from agent.synthesis.graph import SynthesisGraph
from agent.synthesis.classifier import classify_all


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_blink_led_graph() -> SynthesisGraph:
    """Build a simple blink LED circuit: MCU → resistor → LED → GND."""
    g = SynthesisGraph()
    g.add_component({"ref_des": "U1", "id_str": "MCU_ESP32:ESP32", "category": "Microcontroller"})
    g.add_component({"ref_des": "R1", "id_str": "Device:R", "category": "Resistor"})
    g.add_component({"ref_des": "D1", "id_str": "Device:LED", "category": "LED"})
    g.add_component({"ref_des": "C1", "id_str": "Device:C", "category": "Capacitor"})
    g.add_component({"ref_des": "C2", "id_str": "Device:C", "category": "Capacitor"})

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
        "C2:1": {"name": "~", "etype": "passive"},
        "C2:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)
    return g


def _make_power_supply_graph() -> SynthesisGraph:
    """Build a circuit with a regulator: connector → regulator → MCU."""
    g = SynthesisGraph()
    g.add_component({"ref_des": "J1", "id_str": "Connector:USB_C", "category": "Connector"})
    g.add_component({"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3",
                      "category": "Regulator_Linear"})
    g.add_component({"ref_des": "U2", "id_str": "MCU_ESP32:ESP32", "category": "Microcontroller"})
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
        "U2:3": {"name": "GPIO1", "etype": "output"},
        "C1:1": {"name": "~", "etype": "passive"},
        "C1:2": {"name": "~", "etype": "passive"},
        "C2:1": {"name": "~", "etype": "passive"},
        "C2:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)
    return g


# ── Catalog tests ───────────────────────────────────────────────────────────


class TestMotifCatalog:
    def test_catalog_has_18_motifs(self):
        assert len(MOTIF_CATALOG) == 18

    def test_all_motifs_have_names(self):
        for sig in MOTIF_CATALOG:
            assert sig.name, f"Signature missing name: {sig}"
            assert sig.motif_type != MotifType.UNKNOWN, f"{sig.name} has UNKNOWN type"

    def test_all_motifs_have_primary_meta(self):
        for sig in MOTIF_CATALOG:
            assert sig.primary_meta, f"{sig.name} has no primary_meta"

    def test_detection_order_power_first(self):
        """Power motifs should be listed before passive motifs."""
        power_idx = min(i for i, s in enumerate(MOTIF_CATALOG)
                        if s.category == MotifCategory.POWER)
        passive_idx = min(i for i, s in enumerate(MOTIF_CATALOG)
                          if s.category == MotifCategory.PASSIVE)
        assert power_idx < passive_idx, \
            f"Power motif at {power_idx} should come before passive at {passive_idx}"

    def test_ldo_regulator_has_vin_vout_gnd(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.LDO_REGULATOR)
        assert "vin" in sig.primary_pin_roles
        assert "vout" in sig.primary_pin_roles
        assert "ground" in sig.primary_pin_roles

    def test_decoupling_cap_has_power_and_ground_constraints(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.DECOUPLING_CAP)
        net_roles = {c.net_role for c in sig.pin_net_constraints}
        assert "power" in net_roles
        assert "ground" in net_roles

    def test_led_indicator_has_secondary_resistor(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.LED_INDICATOR)
        assert len(sig.secondaries) == 1
        assert "resistor" in sig.secondaries[0].meta.get("passive_class", set())

    def test_rc_filter_has_required_capacitor(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.RC_FILTER)
        assert len(sig.secondaries) == 1
        assert sig.secondaries[0].required is True
        assert "capacitor" in sig.secondaries[0].meta.get("passive_class", set())

    def test_crystal_has_two_load_caps(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.CRYSTAL)
        assert len(sig.secondaries) == 2
        for sec in sig.secondaries:
            assert "capacitor" in sec.meta.get("passive_class", set())

    def test_power_entry_has_three_optional_secondaries(self):
        sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.POWER_ENTRY)
        assert len(sig.secondaries) == 3
        assert all(not sec.required for sec in sig.secondaries)

    def test_all_signatures_have_base_score(self):
        for sig in MOTIF_CATALOG:
            assert sig.base_score > 0, f"{sig.name} has no base_score"

    def test_all_signatures_have_priority(self):
        for sig in MOTIF_CATALOG:
            assert sig.priority > 0, f"{sig.name} has no priority"

    def test_pin_net_constraints_have_required_field(self):
        for sig in MOTIF_CATALOG:
            for c in sig.pin_net_constraints:
                assert hasattr(c, "required"), f"{sig.name} constraint missing required field"


# ── Analyzer tests ──────────────────────────────────────────────────────────


class TestSemanticAnalyzer:
    def test_analyze_blink_led_identifies_controller(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert model.controller == "U1", f"Expected U1, got {model.controller}"

    def test_analyze_blink_led_all_components_have_semantics(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        model = analyze_circuit(g)
        for ref in ("U1", "R1", "D1", "C1", "C2"):
            assert ref in model.components, f"Missing semantic info for {ref}"
            info = model.components[ref]
            assert info.role, f"{ref} has empty role"
            assert info.importance >= 0, f"{ref} has negative importance"

    def test_controller_has_highest_importance(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert model.components["U1"].importance == 1.0

    def test_analyze_power_supply_finds_regulator(self):
        g = _make_power_supply_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "J1:1", "target": "U1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
            {"source": "U2:2", "target": "C2:1", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert model.controller == "U2"
        info = model.components["U1"]
        assert "regulator" in info.role.lower()
        assert info.placement_priority < 5

    def test_analyze_power_supply_detects_power_domains(self):
        g = _make_power_supply_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "J1:1", "target": "U1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
            {"source": "U2:2", "target": "C2:1", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert len(model.power_domains) >= 1

    def test_connector_classified_as_data_usb(self):
        g = _make_power_supply_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "J1:1", "target": "U1:1", "net": "5V"},
            {"source": "J1:3", "target": "U2:3", "net": "USB_DP"},
        ])
        model = analyze_circuit(g)
        info = model.components["J1"]
        assert info.intent == "data_usb", f"Expected data_usb, got {info.intent}"

    def test_analyzer_handles_empty_graph(self):
        g = SynthesisGraph()
        model = analyze_circuit(g)
        assert model.controller is None
        assert len(model.components) == 0

    def test_signal_direction_assignment(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert model.components["U1"].signal_direction == "processing"
        assert model.components["D1"].signal_direction != ""

    def test_placement_priority_controller_is_zero(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "C1:1", "net": "3V3"},
            {"source": "U1:2", "target": "C2:1", "net": "GND"},
        ])
        model = analyze_circuit(g)
        assert model.components["U1"].placement_priority == 0

    def test_power_components_have_top_bottom_signal_flow(self):
        g = _make_power_supply_graph()
        classify_all(g)
        model = analyze_circuit(g)
        flow = model.signal_flow
        assert flow.get("J1") is not None
