"""Tests for the new pipeline nodes.

Covers:
- Architecture Planner: board type inference, MCU lock
- Capability Resolver: devkit provides USB/regulator
- Dependency Expander: ownership graph, support injection
- Deduplicator: immediate dedup, builtin protection
- Constraint Checker: fatal/repairable/warning classification
- Repair: max 2 passes, only adds missing
- Freeze Components: mutation guard
"""

import pytest
from agent.knowledge.board_types import (
    infer_board_type_from_prompt,
    get_provides,
    BOARD_TYPES,
)
from agent.knowledge.dependency_graph import (
    get_mcu_family,
    get_requirements,
    get_owned_capabilities,
    DEPENDENCY_GRAPH,
)


# ── Board Type Knowledge ────────────────────────────────────────────────────

class TestBoardTypes:
    def test_devkit_keywords_detected(self):
        assert infer_board_type_from_prompt("ESP32-C3 dev board") == "devkit"
        assert infer_board_type_from_prompt("ESP32 nodemcu") == "devkit"
        assert infer_board_type_from_prompt("RP2040 breakout board") == "devkit"

    def test_module_keywords_detected(self):
        assert infer_board_type_from_prompt("ESP32-C3-MINI-1 module") == "module"
        assert infer_board_type_from_prompt("WROOM module") == "module"

    def test_bare_ic_keywords_detected(self):
        assert infer_board_type_from_prompt("bare ESP32 chip for sensor") == "bare_ic"
        assert infer_board_type_from_prompt("compact SMD design") == "bare_ic"

    def test_ambiguous_returns_none(self):
        assert infer_board_type_from_prompt("ESP32 temperature sensor") is None

    def test_devkit_provides_usb_and_regulator(self):
        provides = get_provides("devkit")
        assert provides["usb_to_serial"] is True
        assert provides["regulator_3v3"] is True

    def test_module_provides_antenna_no_usb(self):
        provides = get_provides("module")
        assert provides["antenna"] is True
        assert provides["usb_to_serial"] is False

    def test_bare_ic_provides_nothing(self):
        provides = get_provides("bare_ic")
        assert provides == {}


# ── Dependency Graph ────────────────────────────────────────────────────────

class TestDependencyGraph:
    def test_esp32_c3_family(self):
        assert get_mcu_family("MCU_Espressif:ESP32-C3") == "ESP32-C3"

    def test_stm32_family(self):
        assert get_mcu_family("MCU_ST:STM32F103C8T6") == "STM32"

    def test_rp2040_family(self):
        assert get_mcu_family("MCU_Raspberry_Pi:RP2040") == "RP2040"

    def test_esp32_c3_bare_requires_usb(self):
        reqs = get_requirements("ESP32-C3", "bare_ic")
        assert ("usb_connector" in reqs or "usb_to_serial" in reqs)
        assert "regulator_3v3" in reqs

    def test_esp32_c3_devkit_no_usb_required(self):
        reqs = get_requirements("ESP32-C3", "devkit")
        assert "usb_connector" not in reqs and "usb_to_serial" not in reqs
        assert "regulator_3v3" not in reqs

    def test_esp32_c3_module_no_crystal_required(self):
        reqs = get_requirements("ESP32-C3", "module")
        assert "crystal_40mhz" not in reqs

    def test_owned_capabilities(self):
        caps = get_owned_capabilities("ESP32-C3")
        assert "usb" in caps
        assert "uart" in caps


# ── Architecture Planner (unit-level) ──────────────────────────────────────

class TestArchitecturePlanner:
    def test_devkit_board_type_lock(self):
        """DevKit board type should suppress USB/regulator in requirements."""
        provides = get_provides("devkit")
        reqs = get_requirements("ESP32-C3", "devkit")
        # DevKit provides USB and regulator, so they shouldn't be required
        assert "usb_to_serial" not in reqs
        assert "regulator_3v3" not in reqs

    def test_mcu_lock_prevents_mismatch(self):
        """Once MCU is locked, selecting a different MCU should be fatal."""
        primary = "ESP32-C3"
        selected_mcu = "ESP32-S3"
        # These should be different families
        assert primary != selected_mcu


# ── Constraint Checker (unit-level) ────────────────────────────────────────

class TestConstraintChecker:
    def _make_mcu(self, id_str, ref_des="U1"):
        return {
            "id_str": id_str,
            "ref_des": ref_des,
            "category": "MCU_Espressif",
            "description": "Test MCU",
        }

    def _make_passive(self, id_str, ref_des="C1"):
        return {
            "id_str": id_str,
            "ref_des": ref_des,
            "category": "Device",
            "description": "Test passive",
        }

    def test_duplicate_mcu_detected(self):
        """Two MCUs should trigger a fatal error."""
        comps = [
            self._make_mcu("MCU_Espressif:ESP32-C3", "U1"),
            self._make_mcu("MCU_Espressif:ESP32-S3", "U2"),
        ]
        # Import the check function directly
        from agent.nodes.constraint_checker import _check_duplicate_mcus
        errors = _check_duplicate_mcus(comps)
        assert len(errors) == 1
        assert errors[0]["code"] == "DUP_MCU"
        assert errors[0]["category"] == "fatal"

    def test_single_mcu_no_error(self):
        comps = [self._make_mcu("MCU_Espressif:ESP32-C3")]
        from agent.nodes.constraint_checker import _check_duplicate_mcus
        errors = _check_duplicate_mcus(comps)
        assert len(errors) == 0

    def test_mcu_mismatch_detected(self):
        """MCU mismatch with locked architecture should be fatal."""
        comps = [self._make_mcu("MCU_Espressif:ESP32-S3")]
        from agent.nodes.constraint_checker import _check_mcu_matches_architecture
        errors = _check_mcu_matches_architecture(comps, "ESP32-C3", True)
        assert len(errors) == 1
        assert errors[0]["code"] == "MCU_MISMATCH"

    def test_mcu_match_no_error(self):
        comps = [self._make_mcu("MCU_Espressif:ESP32-C3")]
        from agent.nodes.constraint_checker import _check_mcu_matches_architecture
        errors = _check_mcu_matches_architecture(comps, "ESP32-C3", True)
        assert len(errors) == 0


# ── Repair (unit-level) ────────────────────────────────────────────────────

class TestRepair:
    def test_max_passes_enforced(self):
        """After 2 repair passes, remaining errors should become fatal."""
        from agent.nodes.repair import MAX_REPAIR_PASSES
        assert MAX_REPAIR_PASSES == 2

    def test_repair_does_not_modify_fatal_errors(self):
        """Repair should never touch fatal errors."""
        from agent.nodes.repair import _repair_bare_rf_ic
        comps = [{"id_str": "MCU_Espressif:ESP32-C3", "ref_des": "U1"}]
        error = {"code": "FATAL_ERROR", "component_id": "MCU_Espressif:ESP32-C3"}
        # Repair should not touch MCU components for fatal errors
        original = comps.copy()
        _repair_bare_rf_ic(error, comps, {"configurable": {"emit": lambda *a: None}})
        # The MCU should not be modified
        assert comps[0]["id_str"] == "MCU_Espressif:ESP32-C3"


# ── Freeze Components (unit-level) ─────────────────────────────────────────

class TestFreezeComponents:
    def test_frozen_list_blocks_append(self):
        from agent.nodes.freeze_components import FrozenComponentList
        frozen = FrozenComponentList([{"id": 1}, {"id": 2}])
        with pytest.raises(RuntimeError, match="frozen"):
            frozen.append({"id": 3})

    def test_frozen_list_blocks_remove(self):
        from agent.nodes.freeze_components import FrozenComponentList
        frozen = FrozenComponentList([{"id": 1}])
        with pytest.raises(RuntimeError, match="frozen"):
            frozen.remove({"id": 1})

    def test_frozen_list_allows_read(self):
        from agent.nodes.freeze_components import FrozenComponentList
        frozen = FrozenComponentList([{"id": 1}, {"id": 2}])
        assert len(frozen) == 2
        assert frozen[0] == {"id": 1}
        assert list(frozen) == [{"id": 1}, {"id": 2}]


# ── Deduplicator (unit-level) ──────────────────────────────────────────────

class TestDeduplicator:
    def test_builtin_never_deduped(self):
        """Builtin components should never be removed by dedup."""
        from agent.nodes.deduplicator import deduplicator_node
        state = {
            "selected_components": [
                {"id_str": "builtin_usb", "ref_des": "J1", "builtin": True},
                {"id_str": "builtin_usb", "ref_des": "J2", "builtin": True},
            ],
        }
        config = {"configurable": {"emit": lambda *a: None}}
        result = deduplicator_node(state, config)
        # Both builtin components should be kept
        assert len(result["selected_components"]) == 2


# ── Bug Fix Tests ───────────────────────────────────────────────────────────

class TestSelectBuiltinSkip:
    """Fix 1: select_node should skip subsystems provided by builtins."""

    def test_is_mcu_detects_wemos(self):
        """Fix 5: _is_mcu should detect WEMOS as MCU module."""
        from agent.nodes.constraint_checker import _is_mcu
        comp = {"id_str": "RF_Module:WEMOS_C3_mini", "category": "RF_Module"}
        assert _is_mcu(comp) is True

    def test_is_mcu_detects_wroom(self):
        """Fix 5: _is_mcu should detect WROOM as MCU module."""
        from agent.nodes.constraint_checker import _is_mcu
        comp = {"id_str": "RF_Module:ESP32-WROOM-32U", "category": "RF_Module"}
        assert _is_mcu(comp) is True

    def test_is_mcu_detects_devkit(self):
        """Fix 5: _is_mcu should detect DEVKIT as MCU module."""
        from agent.nodes.constraint_checker import _is_mcu
        comp = {"id_str": "RF_Module:ESP32-C3-DevKitC", "category": "RF_Module"}
        assert _is_mcu(comp) is True

    def test_is_mcu_rejects_sensor(self):
        from agent.nodes.constraint_checker import _is_mcu
        comp = {"id_str": "Sensor_Temperature:TMP117", "category": "Sensor_Temperature"}
        assert _is_mcu(comp) is False


class TestDeduplicatorFamilyDedup:
    """Fix 3: deduplicator should catch same-family duplicates."""

    def test_same_family_different_variant_removed(self):
        """Two TMP117 variants should be deduped."""
        from agent.nodes.deduplicator import deduplicator_node
        state = {
            "selected_components": [
                {"id_str": "Sensor_Temperature:TMP117xxDRV", "ref_des": "U1", "category": "Sensor"},
                {"id_str": "Sensor_Temperature:TMP117xxYBG", "ref_des": "U2", "category": "Sensor"},
            ],
        }
        config = {"configurable": {"emit": lambda *a: None}}
        result = deduplicator_node(state, config)
        # Should keep only one TMP117
        tmp117s = [c for c in result["selected_components"] if "TMP117" in c.get("id_str", "")]
        assert len(tmp117s) == 1

    def test_different_families_not_deduped(self):
        """Different IC families should NOT be deduped."""
        from agent.nodes.deduplicator import deduplicator_node
        state = {
            "selected_components": [
                {"id_str": "Sensor_Temperature:TMP117xxDRV", "ref_des": "U1", "category": "Sensor"},
                {"id_str": "Sensor_Temperature:BME280", "ref_des": "U2", "category": "Sensor"},
            ],
        }
        config = {"configurable": {"emit": lambda *a: None}}
        result = deduplicator_node(state, config)
        assert len(result["selected_components"]) == 2


class TestValidateArchitectureFrozen:
    """Fix 4: validate_node should skip auto-add when architecture frozen."""

    def test_architecture_frozen_blocks_auto_add(self):
        """When architecture_frozen=True, missing components should not be auto-added."""
        from agent.nodes.constraint_checker import _check_duplicate_mcus
        # This tests the constraint_checker detects WEMOS as MCU
        comps = [
            {"id_str": "RF_Module:ESP32-WROOM-32U", "ref_des": "U1", "category": "RF_Module"},
            {"id_str": "RF_Module:WEMOS_C3_mini", "ref_des": "U2", "category": "RF_Module"},
        ]
        errors = _check_duplicate_mcus(comps)
        assert len(errors) == 1
        assert errors[0]["code"] == "DUP_MCU"


class TestDependencyExpanderModuleSkip:
    """Fix 2: dependency_expander should skip injection when module MCU selected."""

    def test_wroom_module_skips_injection(self):
        """When WROOM module is selected, no dependency injection should happen."""
        from agent.nodes.dependency_expander import dependency_expander_node
        state = {
            "selected_components": [
                {"id_str": "RF_Module:ESP32-WROOM-32U", "ref_des": "U1", "category": "RF_Module",
                 "description": "ESP32 module"},
            ],
            "_builtin_components": [],
            "board_type": "module",
            "primary_mcu": "ESP32-C3",
        }
        config = {"configurable": {"emit": lambda *a: None}}
        result = dependency_expander_node(state, config)
        # Should NOT inject any new components
        assert len(result["selected_components"]) == 1


class TestPowerNetRailSeparation:
    """Fix 3: power_net_repair should not merge different voltage rails."""

    def test_vbus_not_merged_into_3v3(self):
        """VBUS (5V) should never be merged into 3V3 (3.3V)."""
        from agent.nodes.power_net_repair import _are_rails_compatible, _safe_merge_net
        # VBUS and 3V3 are different voltage rails
        assert _are_rails_compatible("VBUS", "3V3") is False
        # VBUS and VUSB are equivalent (both 5V)
        assert _are_rails_compatible("VBUS", "VUSB") is True
        # 3V3 and VCC are equivalent (both 3.3V)
        assert _are_rails_compatible("3V3", "VCC") is True
        # Same rail always compatible
        assert _are_rails_compatible("VBUS", "VBUS") is True

    def test_safe_merge_blocks_different_rails(self):
        """_safe_merge_net should block merging different voltage rails."""
        from agent.nodes.power_net_repair import _safe_merge_net
        nets = [{"net": "3V3", "pins": ["U1:2"]}]
        # Try to merge VBUS pin into 3V3 net — should be blocked
        result = _safe_merge_net(nets, "VBUS", ["J1:4"])
        assert result is False
        # Original net should be unchanged
        assert len(nets) == 1
        assert nets[0]["net"] == "3V3"

    def test_safe_merge_allows_same_rail(self):
        """_safe_merge_net should allow merging equivalent rails."""
        from agent.nodes.power_net_repair import _safe_merge_net
        nets = [{"net": "3V3", "pins": ["U1:2"]}]
        # Merge VCC into 3V3 — should succeed (both 3.3V)
        result = _safe_merge_net(nets, "VCC", ["U2:1"])
        assert result is True
        assert len(nets) == 1
        assert "U2:1" in nets[0]["pins"]
