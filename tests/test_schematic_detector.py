"""Tests for motif detection engine (matcher + detector)."""

import pytest
from agent.schematic.matcher import (
    CandidateMatch,
    discover_candidates,
    matches_meta,
    has_pin_roles,
    check_pin_net_constraints,
    find_secondaries,
    calculate_score,
)
from agent.schematic.detector import (
    detect_motifs,
    resolve_conflicts,
    score_candidates,
    find_orphan_components,
)
from agent.schematic.catalog import (
    MOTIF_CATALOG,
    DECOUPLING_CAP,
    LDO_REGULATOR,
    LED_INDICATOR,
    RC_FILTER,
    PULL_UP,
    PULL_DOWN,
)
from agent.schematic.schematic_types import (
    MotifType,
    MotifCategory,
    PinNetConstraint,
    SecondarySpec,
    MotifSignature,
)
from agent.synthesis.graph import SynthesisGraph
from agent.synthesis.classifier import classify_all


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_blink_led_graph() -> SynthesisGraph:
    """MCU(U1) → R1 → D1(LED) → GND. Two decoupling caps on 3V3."""
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
    """J1(USB) → U1(regulator) → U2(MCU). Caps on each side."""
    g = SynthesisGraph()
    g.add_component({"ref_des": "J1", "id_str": "Connector:USB_C", "category": "Connector"})
    g.add_component({"ref_des": "F1", "id_str": "Device:Fuse", "category": "Fuse"})
    g.add_component({"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3",
                      "category": "Regulator_Linear"})
    g.add_component({"ref_des": "U2", "id_str": "MCU_ESP32:ESP32", "category": "Microcontroller"})
    g.add_component({"ref_des": "C1", "id_str": "Device:C", "category": "Capacitor"})
    g.add_component({"ref_des": "C2", "id_str": "Device:C", "category": "Capacitor"})
    g.add_component({"ref_des": "C3", "id_str": "Device:C", "category": "Capacitor"})
    pins = {
        "J1:1": {"name": "VBUS", "etype": "power_in"},
        "J1:2": {"name": "GND", "etype": "passive"},
        "J1:3": {"name": "D+", "etype": "bidirectional"},
        "J1:4": {"name": "D-", "etype": "bidirectional"},
        "F1:1": {"name": "~", "etype": "passive"},
        "F1:2": {"name": "~", "etype": "passive"},
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
        "C3:1": {"name": "~", "etype": "passive"},
        "C3:2": {"name": "~", "etype": "passive"},
    }
    for pk, pd in pins.items():
        ref = pk.split(":")[0]
        g.add_pin(ref, pk, pd)
    return g


# ── Matcher unit tests ──────────────────────────────────────────────────────


class TestMatchesMeta:
    def _classified_r1(self):
        g = _make_blink_led_graph()
        classify_all(g)
        return g.components["R1"]

    def test_empty_predicates(self):
        assert matches_meta(self._classified_r1(), {}) is True

    def test_class_match(self):
        assert matches_meta(self._classified_r1(), {"passive_class": {"resistor"}}) is True

    def test_class_mismatch(self):
        assert matches_meta(self._classified_r1(), {"passive_class": {"capacitor"}}) is False

    def test_multiple_accepted_values(self):
        assert matches_meta(self._classified_r1(), {"passive_class": {"resistor", "capacitor"}}) is True

    def test_unknown_key_returns_false(self):
        assert matches_meta(self._classified_r1(), {"nonexistent_key": {"value"}}) is False


class TestHasPinRoles:
    def test_empty_roles(self):
        comp = _make_blink_led_graph().components["D1"]
        assert has_pin_roles(comp, set()) is True

    def test_led_has_anode_cathode(self):
        g = _make_blink_led_graph()
        classify_all(g)
        comp = g.components["D1"]
        assert has_pin_roles(comp, {"anode", "cathode"}) is True

    def test_resistor_no_special_roles(self):
        g = _make_blink_led_graph()
        classify_all(g)
        comp = g.components["R1"]
        assert has_pin_roles(comp, {"anode"}) is False

    def test_regulator_has_vin_vout(self):
        g = _make_power_supply_graph()
        classify_all(g)
        comp = g.components["U1"]
        assert has_pin_roles(comp, {"vin", "vout"}) is True


class TestPinNetConstraints:
    def test_decoupling_cap_on_power_and_ground(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U1:1", "net": "3V3"},
            {"source": "C1:2", "target": "U1:2", "net": "GND"},
        ])
        comp = g.components["C1"]
        constraints = [
            PinNetConstraint(pin_role="", net_role="power"),
            PinNetConstraint(pin_role="", net_role="ground"),
        ]
        assert check_pin_net_constraints(comp, constraints, g) is True

    def test_cap_not_on_power_fails(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U1:3", "net": "GPIO2"},
            {"source": "C1:2", "target": "U1:2", "net": "GND"},
        ])
        comp = g.components["C1"]
        constraints = [
            PinNetConstraint(pin_role="", net_role="power"),
            PinNetConstraint(pin_role="", net_role="ground"),
        ]
        assert check_pin_net_constraints(comp, constraints, g) is False

    def test_no_constraints_always_passes(self):
        g = _make_blink_led_graph()
        classify_all(g)
        comp = g.components["C1"]
        assert check_pin_net_constraints(comp, [], g) is True

    def test_optional_constraint_failure_allowed(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U1:1", "net": "3V3"},
            {"source": "C1:2", "target": "U1:2", "net": "GND"},
        ])
        comp = g.components["C1"]
        constraints = [
            PinNetConstraint(pin_role="", net_role="power"),
            PinNetConstraint(pin_role="", net_role="analog", required=False),
        ]
        assert check_pin_net_constraints(comp, constraints, g) is True


class TestFindSecondaries:
    def test_led_indicator_finds_resistor(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        specs = [
            SecondarySpec(
                meta={"passive_class": {"resistor"}},
                required=True,
                label="current_limit_resistor",
            ),
        ]
        comp = g.components["D1"]
        result = find_secondaries(comp, specs, g)
        assert "current_limit_resistor" in result
        assert result["current_limit_resistor"] == "R1"

    def test_required_secondary_missing_fails(self):
        g = _make_power_supply_graph()
        classify_all(g)
        specs = [
            SecondarySpec(
                meta={"passive_class": {"resistor"}},
                required=True,
                label="sense_resistor",
            ),
        ]
        comp = g.components["U1"]
        result = find_secondaries(comp, specs, g)
        assert result == {}

    def test_optional_secondary_missing_ok(self):
        g = _make_power_supply_graph()
        classify_all(g)
        g.import_llm_nets([])
        specs = [
            SecondarySpec(
                meta={"passive_class": {"diode"}},
                required=False,
                label="flyback_diode",
            ),
        ]
        comp = g.components["U1"]
        result = find_secondaries(comp, specs, g)
        assert result == {}


class TestDiscoverCandidates:
    def test_discover_decoupling_caps(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U1:1", "net": "3V3"},
            {"source": "C1:2", "target": "U1:2", "net": "GND"},
            {"source": "C2:1", "target": "U1:1", "net": "3V3"},
            {"source": "C2:2", "target": "U1:2", "net": "GND"},
        ])
        candidates = discover_candidates(g, DECOUPLING_CAP)
        assert len(candidates) == 2
        refs = {c.primary for c in candidates}
        assert "C1" in refs
        assert "C2" in refs

    def test_discover_no_matches(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([])
        candidates = discover_candidates(g, LDO_REGULATOR)
        assert len(candidates) == 0

    def test_discover_led_indicator(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        candidates = discover_candidates(g, LED_INDICATOR)
        assert len(candidates) == 1
        assert candidates[0].primary == "D1"
        assert "R1" in candidates[0].secondaries.values()

    def test_discover_pull_up(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "R1:1", "target": "U1:1", "net": "3V3"},
            {"source": "R1:2", "target": "U1:3", "net": "GPIO2"},
        ])
        candidates = discover_candidates(g, PULL_UP)
        assert len(candidates) == 1
        assert candidates[0].primary == "R1"


# ── Conflict resolution tests ──────────────────────────────────────────────


class TestResolveConflicts:
    def test_no_conflicts(self):
        c1 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 50.0)
        c2 = CandidateMatch(DECOUPLING_CAP, "C2", {}, 45.0)
        resolved = resolve_conflicts([c1, c2])
        assert len(resolved) == 2

    def test_conflict_highest_score_wins(self):
        c1 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 50.0)
        c2 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 45.0)
        resolved = resolve_conflicts([c1, c2])
        assert len(resolved) == 1
        assert resolved[0].primary == "C1"

    def test_overlapping_components_dropped(self):
        """Two motifs claiming the same cap — higher score keeps it."""
        c1 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 50.0)
        c2 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 40.0)
        resolved = resolve_conflicts([c1, c2])
        assert len(resolved) == 1
        assert resolved[0].score == 50.0

    def test_usb_tvs_conflict_scenario(self):
        """TVS claimed by both USB and power entry — higher score wins."""
        power_entry_sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.POWER_ENTRY)
        usb_sig = next(s for s in MOTIF_CATALOG if s.motif_type == MotifType.USB_INTERFACE)
        cand_a = CandidateMatch(power_entry_sig, "J1", {"fuse": "F1"}, 70.0)
        cand_b = CandidateMatch(usb_sig, "J1", {}, 65.0)
        resolved = resolve_conflicts([cand_a, cand_b])
        assert len(resolved) == 1
        assert resolved[0].score == 70.0

    def test_deterministic_order_same_score(self):
        """Same score should resolve deterministically by name."""
        c1 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 50.0)
        c2 = CandidateMatch(DECOUPLING_CAP, "C2", {}, 50.0)
        c3 = CandidateMatch(DECOUPLING_CAP, "C1", {}, 50.0)
        resolved1 = resolve_conflicts([c1, c2, c3])
        resolved2 = resolve_conflicts([c3, c2, c1])
        assert len(resolved1) == len(resolved2)
        names1 = [c.signature.name for c in resolved1]
        names2 = [c.signature.name for c in resolved2]
        assert names1 == names2


# ── Full detection pipeline tests ──────────────────────────────────────────


class TestDetectMotifs:
    def test_detect_on_blink_led(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "C1:1", "net": "3V3"},
            {"source": "U1:2", "target": "C1:2", "net": "GND"},
            {"source": "U1:1", "target": "C2:1", "net": "3V3"},
            {"source": "U1:2", "target": "C2:2", "net": "GND"},
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        motifs = detect_motifs(g)
        motif_types = {m.motif_type for m in motifs}
        assert len(motifs) >= 2
        assert MotifType.DECOUPLING_CAP in motif_types
        assert MotifType.LED_INDICATOR in motif_types

    def test_no_double_claimed_components(self):
        """Every component should appear in at most one motif."""
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "C1:1", "net": "3V3"},
            {"source": "U1:2", "target": "C1:2", "net": "GND"},
            {"source": "U1:3", "target": "R1:1", "net": "LED_DRV"},
            {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},
            {"source": "D1:2", "target": "U1:2", "net": "GND"},
        ])
        motifs = detect_motifs(g)
        all_claimed: set[str] = set()
        for motif in motifs:
            overlap = all_claimed & set(motif.components)
            assert not overlap, f"Component(s) {overlap} claimed by multiple motifs"
            all_claimed.update(motif.components)

    def test_detect_on_power_supply(self):
        g = _make_power_supply_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "J1:1", "target": "F1:1", "net": "VBUS"},
            {"source": "F1:2", "target": "U1:1", "net": "5V"},
            {"source": "U1:2", "target": "C1:1", "net": "GND"},
            {"source": "U1:3", "target": "U2:1", "net": "3V3"},
            {"source": "U2:2", "target": "C2:1", "net": "GND"},
            {"source": "U2:1", "target": "C3:1", "net": "3V3"},
            {"source": "U2:2", "target": "C3:2", "net": "GND"},
        ])
        motifs = detect_motifs(g)
        types = {m.motif_type for m in motifs}
        assert MotifType.LDO_REGULATOR in types, f"LDO not found. Types: {types}"
        assert len(motifs) >= 2

    def test_empty_graph_returns_empty(self):
        g = SynthesisGraph()
        motifs = detect_motifs(g)
        assert motifs == []

    def test_no_nets_returns_empty(self):
        g = _make_blink_led_graph()
        classify_all(g)
        motifs = detect_motifs(g)
        assert motifs == []  # no nets → no pin-net constraints satisfied

    def test_catalog_parameter(self):
        """Should work with a subset of the catalog."""
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "C1:1", "target": "U1:1", "net": "3V3"},
            {"source": "C1:2", "target": "U1:2", "net": "GND"},
        ])
        motifs = detect_motifs(g, catalog=[DECOUPLING_CAP])
        assert len(motifs) == 1
        assert motifs[0].motif_type == MotifType.DECOUPLING_CAP


class TestOrphans:
    def test_find_orphans(self):
        g = _make_blink_led_graph()
        classify_all(g)
        g.import_llm_nets([
            {"source": "U1:1", "target": "C1:1", "net": "3V3"},
            {"source": "U1:2", "target": "C1:2", "net": "GND"},
        ])
        motifs = detect_motifs(g)
        orphans = find_orphan_components(g, motifs)
        # U1 (MCU), R1, D1, C2 are not in any motif
        assert "U1" in orphans
        assert "C1" not in orphans

    def test_all_components_orphan(self):
        g = SynthesisGraph()
        g.add_component({"ref_des": "R1", "id_str": "Device:R", "category": "Resistor"})
        g.add_pin("R1", "R1:1", {"name": "~", "etype": "passive"})
        g.add_pin("R1", "R1:2", {"name": "~", "etype": "passive"})
        classify_all(g)
        motifs = detect_motifs(g)
        orphans = find_orphan_components(g, motifs)
        assert "R1" in orphans
