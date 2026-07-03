"""Tests for the canonical synthesis graph model and pipeline."""

import pytest
from agent.synthesis.graph import (
    SynthesisGraph, PinNode, ComponentNode, NetNode,
    ConstraintEdge, ConstraintType, PinRole, NetRole,
)
from agent.synthesis.classifier import (
    classify_all, classify_component, classify_passive, classify_pins,
)
from agent.synthesis.topology import (
    TopologyRule, match_and_constrain,
)
from agent.synthesis.engine import (
    validate_constraints, suggest_repairs,
)
from agent.synthesis.validation import (
    validate_circuit,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_graph() -> SynthesisGraph:
    g = SynthesisGraph()
    g.add_component({"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3",
                      "category": "Regulator_Linear"})
    g.add_component({"ref_des": "U2", "id_str": "MCU_ESP32:ESP32",
                      "category": "Microcontroller"})
    g.add_component({"ref_des": "D1", "id_str": "Device:LED",
                      "category": "LED"})
    g.add_component({"ref_des": "R1", "id_str": "Device:R",
                      "category": "Resistor"})
    g.add_component({"ref_des": "C1", "id_str": "Device:C",
                      "category": "Capacitor"})
    g.add_component({"ref_des": "C2", "id_str": "Device:C",
                      "category": "Capacitor"})

    pins = {
        "U1:1": {"name": "VIN", "etype": "power_in"},
        "U1:2": {"name": "GND", "etype": "passive"},
        "U1:3": {"name": "VOUT", "etype": "power_out"},
        "U2:1": {"name": "3V3", "etype": "power_in"},
        "U2:2": {"name": "GND", "etype": "passive"},
        "U2:3": {"name": "GPIO2", "etype": "output"},
        "D1:1": {"name": "A", "etype": "passive"},
        "D1:2": {"name": "K", "etype": "passive"},
        "R1:1": {"name": "~", "etype": "passive"},
        "R1:2": {"name": "~", "etype": "passive"},
        "C1:1": {"name": "~", "etype": "passive"},
        "C1:2": {"name": "~", "etype": "passive"},
        "C2:1": {"name": "~", "etype": "passive"},
        "C2:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)
    return g


# ── PinRole classification ──────────────────────────────────────────────────

class TestPinRole:
    def test_from_pin_name_nc(self):
        assert PinRole.from_pin_name("NC") == PinRole.NC
        assert PinRole.from_pin_name("NO_CONNECT") == PinRole.NC
        assert PinRole.from_pin_name("") == PinRole.NC

    def test_from_pin_name_ground(self):
        assert PinRole.from_pin_name("GND") == PinRole.GND
        assert PinRole.from_pin_name("AGND") == PinRole.GND
        assert PinRole.from_pin_name("PGND") == PinRole.GND

    def test_from_pin_name_power(self):
        assert PinRole.from_pin_name("VCC") == PinRole.POWER_IN
        assert PinRole.from_pin_name("3V3") == PinRole.POWER_IN
        assert PinRole.from_pin_name("VIN") == PinRole.VIN

    def test_from_pin_name_special(self):
        assert PinRole.from_pin_name("SDA") == PinRole.SDA
        assert PinRole.from_pin_name("SCL") == PinRole.SCL
        assert PinRole.from_pin_name("TXD0") == PinRole.TX
        assert PinRole.from_pin_name("RXD0") == PinRole.RX
        assert PinRole.from_pin_name("RESET") == PinRole.RESET
        assert PinRole.from_pin_name("EN") == PinRole.EN

    def test_from_pin_name_anode_cathode(self):
        assert PinRole.from_pin_name("A") == PinRole.ANODE
        assert PinRole.from_pin_name("K") == PinRole.CATHODE

    def test_from_pin_name_fallback_to_etype(self):
        assert PinRole.from_pin_name("SOME_PIN", etype="input") == PinRole.INPUT
        assert PinRole.from_pin_name("SOME_PIN", etype="output") == PinRole.OUTPUT
        assert PinRole.from_pin_name("SOME_PIN", etype="bidirectional") == PinRole.BIDIRECTIONAL

    def test_from_pin_name_unknown(self):
        assert PinRole.from_pin_name("SOME_WEIRD_PIN") == PinRole.UNUSED


# ── NetRole classification ──────────────────────────────────────────────────

class TestNetRole:
    def test_ground_nets(self):
        assert NetRole.from_net_name("GND") == NetRole.GROUND
        assert NetRole.from_net_name("AGND") == NetRole.GROUND

    def test_power_nets(self):
        assert NetRole.from_net_name("VCC") == NetRole.POWER
        assert NetRole.from_net_name("3V3") == NetRole.POWER
        assert NetRole.from_net_name("5V") == NetRole.POWER

    def test_analog(self):
        assert NetRole.from_net_name("ADC1") == NetRole.ANALOG
        assert NetRole.from_net_name("ANALOG_IN") == NetRole.ANALOG

    def test_communication(self):
        assert NetRole.from_net_name("I2C_SCL") == NetRole.COMMUNICATION
        assert NetRole.from_net_name("UART_TX") == NetRole.COMMUNICATION

    def test_signal_fallback(self):
        assert NetRole.from_net_name("GPIO2") == NetRole.SIGNAL
        assert NetRole.from_net_name("") == NetRole.SIGNAL


# ── SynthesisGraph construction ─────────────────────────────────────────────

class TestSynthesisGraph:
    def test_add_component(self):
        g = SynthesisGraph()
        g.add_component({"ref_des": "R1", "id_str": "Device:R"})
        assert "R1" in g.components
        assert g.components["R1"].id_str == "Device:R"

    def test_add_pin(self):
        g = SynthesisGraph()
        g.add_component({"ref_des": "R1", "id_str": "Device:R"})
        g.add_pin("R1", "R1:1", {"name": "~", "etype": "passive"})
        assert "R1:1" in g.components["R1"].pins
        assert g.components["R1"].pins["R1:1"].role is not None

    def test_add_pin_missing_component(self):
        g = SynthesisGraph()
        result = g.add_pin("R1", "R1:1", {"name": "~"})
        assert result is None

    def test_get_or_create_net(self):
        g = SynthesisGraph()
        net = g.get_or_create_net("GND")
        assert net.role == NetRole.GROUND
        # Same name returns same object
        assert g.get_or_create_net("GND") is net

    def test_net_role_from_name(self):
        g = SynthesisGraph()
        assert g.get_or_create_net("3V3").role == NetRole.POWER
        assert g.get_or_create_net("SIGNAL_X").role == NetRole.SIGNAL

    def test_import_llm_nets(self):
        g = SynthesisGraph()
        g.import_llm_nets([
            {"source": "U1:1", "target": "R1:1", "net": "VOUT"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_SIGNAL"},
        ])
        assert "VOUT" in g.nets
        assert "LED_SIGNAL" in g.nets
        assert "U1:1" in g.nets["VOUT"].pins
        assert "R1:1" in g.nets["VOUT"].pins

    def test_nets_by_role(self):
        g = SynthesisGraph()
        g.get_or_create_net("GND")
        g.get_or_create_net("3V3")
        g.get_or_create_net("SIG")
        assert len(g.nets_by_role(NetRole.GROUND)) == 1
        assert len(g.nets_by_role(NetRole.POWER)) == 1
        assert len(g.nets_by_role(NetRole.SIGNAL)) == 1

    def test_pin_role_query(self):
        g = SynthesisGraph()
        g.add_component({"ref_des": "U1", "id_str": "Device:LED"})
        g.add_pin("U1", "U1:1", {"name": "A", "etype": "passive"})
        assert g.pin_role("U1:1") == PinRole.ANODE


# ── Classifier ──────────────────────────────────────────────────────────────

class TestClassifier:
    def test_classify_passive_resistor(self):
        c = ComponentNode("R1", "Device:R")
        assert classify_passive(c) == "resistor"

    def test_classify_passive_capacitor(self):
        c = ComponentNode("C1", "Device:C")
        assert classify_passive(c) == "capacitor"

    def test_classify_passive_led(self):
        c = ComponentNode("D1", "Device:LED")
        assert classify_passive(c) == "led"

    def test_classify_passive_unknown(self):
        c = ComponentNode("U1", "MCU:ESP32")
        assert classify_passive(c) is None

    def test_classify_component_linear_regulator(self):
        c = ComponentNode("U1", "power:AMS1117", library="Regulator_Linear")
        assert classify_component(c) == "linear_regulator"

    def test_classify_component_mcu(self):
        c = ComponentNode("U1", "MCU:ESP32", library="MCU_ESP32")
        assert classify_component(c) == "microcontroller"

    def test_classify_all_assigns_roles(self):
        g = _make_graph()
        classify_all(g)
        # U1 pins
        assert g.pin_role("U1:1") == PinRole.VIN
        assert g.pin_role("U1:2") == PinRole.GND
        assert g.pin_role("U1:3") == PinRole.VOUT
        # D1 pins
        assert g.pin_role("D1:1") == PinRole.ANODE
        assert g.pin_role("D1:2") == PinRole.CATHODE
        # Unknown passive pins stay UNUSED
        assert g.pin_role("R1:1") == PinRole.NC  # "~" is KiCad's no-name placeholder
        assert g.pin_role("C1:1") == PinRole.NC
        # Metadata set
        assert g.components["D1"].metadata.get("passive_class") == "led"
        assert g.components["R1"].metadata.get("passive_class") == "resistor"
        assert g.components["C1"].metadata.get("passive_class") == "capacitor"


# ── Topology matching ───────────────────────────────────────────────────────

class TestTopologyMatching:
    def test_indicator_led_matched(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "D1:1", "target": "R1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U2:2", "net": "GND"},
            {"source": "R1:2", "target": "U2:3", "net": "GPIO2"},
        ])
        match_and_constrain(g)
        d1_pins = {"D1:1", "D1:2"}
        d1_constraints = [c for c in g.constraints
                          if c.source_pin in d1_pins or (c.target_pin and c.target_pin in d1_pins)]
        assert len(d1_constraints) >= 2  # at least LOAD + GROUNDED_BY

    def test_linear_regulator_matched(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "J1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
        ])
        match_and_constrain(g)
        u1_constraints = [c for c in g.constraints if c.source_pin == "U1:1"]
        assert any(c.type == ConstraintType.POWERED_BY for c in u1_constraints), (
            f"No POWERED_BY constraint for U1:1. All constraints: {[(c.type, c.source_pin) for c in g.constraints]}"
        )

    def test_bypass_capacitor_matched(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U2:1", "net": "3V3"},
            {"source": "C1:2", "target": "U2:2", "net": "GND"},
        ])
        match_and_constrain(g)
        c1_constraints = [c for c in g.constraints if c.source_pin in ("C1:1", "C1:2")]
        # Should have at least DECOUPLES (to power) and GROUNDED_BY
        types = {c.type for c in c1_constraints}
        assert ConstraintType.DECOUPLES in types
        assert ConstraintType.GROUNDED_BY in types

    def test_custom_topology_rule(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([{"source": "U2:1", "target": "C1:1", "net": "3V3"}])
        custom = TopologyRule(
            name="test_rule",
            comp_meta={"passive_class": {"capacitor"}},
            pin_roles=set(),
            net_role_map={},
            constraints=[{
                "type": ConstraintType.DECOUPLES,
                "source_role": None,
                "net_role": NetRole.POWER,
            }],
        )
        match_and_constrain(g, topologies=[custom])
        assert any(
            c.type == ConstraintType.DECOUPLES
            for c in g.constraints
        )

    def test_no_match_if_no_llm_nets(self):
        g = _make_graph()
        classify_all(g)
        match_and_constrain(g)
        # Requirements are generated even without LLM nets (constraints are requirements, not facts)
        # The linear regulator rule matches U1 and generates POWERED_BY for VIN and VOUT even
        # without any nets present
        assert len(g.constraints) >= 1
        vins = [c for c in g.constraints if c.source_pin == "U1:1"]
        assert any(c.type == ConstraintType.POWERED_BY for c in vins)


# ── Constraint validation ───────────────────────────────────────────────────

class TestConstraintValidation:
    def test_validate_power_constraint_satisfied(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "J1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
        ])
        match_and_constrain(g)
        violations = validate_constraints(g)
        # U1:1 on 5V (power), U1:2 on GND (ground) → satisfied
        pin1_violations = [v for v in violations if "U1:1" in v.description]
        assert len(pin1_violations) == 0

    def test_validate_constraint_violation(self):
        g = _make_graph()
        classify_all(g)
        # U1:1 (VIN) connected to a net that is NOT a power net → constraint violation
        g.import_llm_nets([
            {"source": "U1:1", "target": "J1:1", "net": "RAW_DC_IN"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
        ])
        match_and_constrain(g)
        violations = validate_constraints(g)
        pin1_violations = [v for v in violations if "U1:1" in v.description]
        assert len(pin1_violations) >= 1, (
            f"No violations for U1:1. All violations: {[v.description for v in violations]}"
        )

    def test_suggest_repairs(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "J1:1", "net": "RAW_DC_IN"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
        ])
        match_and_constrain(g)
        violations = validate_constraints(g)
        repairs = suggest_repairs(violations)
        assert len(repairs) >= 1


# ── SynthesisGraph serialisation ────────────────────────────────────────────

class TestSerialisation:
    def test_serialise_round_trip(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "R1:1", "net": "5V"},
            {"source": "D1:2", "target": "U2:2", "net": "GND"},
        ])
        g.import_power_pins([
            {"pin": "C1:1", "net": "3V3"},
        ])

        serialised = {
            "components": {r: {"ref_des": r, "id_str": c.id_str, "library": c.library,
                               "category": c.category, "user_locked": c.user_locked,
                               "pins": {pk: {"name": p.name, "role": p.role.value, "etype": p.etype}
                                        for pk, p in c.pins.items()}}
                           for r, c in g.components.items()},
            "nets": {n: {"name": n, "role": nr.role.value, "pins": sorted(nr.pins)}
                     for n, nr in g.nets.items()},
            "constraints": [{"type": ct.type.value, "source_pin": ct.source_pin,
                              "target_pin": ct.target_pin, "metadata": ct.metadata}
                             for ct in g.constraints],
        }

        assert "U1" in serialised["components"]
        assert "GND" in serialised["nets"]
        # Verify a pin role survived serialization
        assert serialised["components"]["D1"]["pins"]["D1:1"]["role"] == "anode"


# ── Full validation pipeline ────────────────────────────────────────────────

class TestValidationPipeline:
    def test_validate_circuit_empty(self):
        g = SynthesisGraph()
        issues = validate_circuit(g)
        assert isinstance(issues, list)

    def test_validate_circuit_with_unconnected_power(self):
        g = _make_graph()
        classify_all(g)
        # No nets imported at all
        issues = validate_circuit(g)
        severities = {i["severity"] for i in issues}
        assert "critical" in severities

    def test_validate_circuit_with_power(self):
        g = _make_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "J1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
        ])
        issues = validate_circuit(g)
        assert len(issues) >= 0  # no crashes
