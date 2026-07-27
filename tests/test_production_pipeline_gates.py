"""Regression tests for non-negotiable production pipeline gates."""

from agent.knowledge.board_types import infer_board_type_from_prompt
from agent.builder import error_end_node
from agent.nodes.ask_validation_help import ask_validation_help_node
from agent.nodes.netlist import _preassign_power_nets
from agent.nodes.pcb_layout import _build_nets_from_netlist
from agent.pin_matcher import match_pins
from agent.route_utils import _route_after_erc
from agent.nodes.constraint_checker import _check_missing_programming_header
from agent.nodes.repair import _repair_missing_programming_header


def test_generic_board_request_is_not_misclassified_as_a_devkit():
    assert infer_board_type_from_prompt("design a temperature sensor board") is None


def test_validation_help_cannot_clear_required_errors():
    state = {
        "validation_errors": ["OLED display is missing"],
        "validation_warnings": [],
        "selected_components": [],
    }
    config = {
        "configurable": {
            "emit": lambda *_: None,
            "validation_help_result": {"action": "force"},
        }
    }

    result = ask_validation_help_node(state, config)

    assert "error" in result
    assert "OLED display is missing" in result["error"]


def test_usb_input_and_3v3_output_domains_are_explicit():
    pins = {
        "J1:A4": {"name": "VBUS"},
        "U1:1": {"name": "VIN"},
        "U2:1": {"name": "VDD"},
        "U1:2": {"name": "GND"},
        "U2:2": {"name": "GND"},
    }
    components = [
        {"ref_des": "J1", "id_str": "Connector:USB_C_Receptacle_USB2.0_16P"},
        {"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3"},
        {"ref_des": "U2", "id_str": "MCU_Espressif:ESP32-C3"},
    ]

    nets, _, groups = _preassign_power_nets(pins, components)

    assert groups["VBUS"] == ["J1:A4", "U1:1"]
    assert groups["3V3"] == ["U2:1"]
    assert {net["net"] for net in nets} == {"VBUS", "3V3", "GND"}


def test_usb_cc_pins_are_connected_through_resistors_not_directly_to_ground():
    components = [
        {"ref_des": "J1", "id_str": "Connector:USB_C_Receptacle_USB2.0_16P"},
        {"ref_des": "R1", "id_str": "Device:R_Small", "value": "5.1k", "description": "USB-C CC pull-down"},
        {"ref_des": "R2", "id_str": "Device:R_Small", "value": "5.1k", "description": "USB-C CC pull-down"},
    ]
    pins = {
        "J1:A5": {"name": "CC1"},
        "J1:B5": {"name": "CC2"},
        "J1:A4": {"name": "VBUS"},
        "J1:A1": {"name": "GND"},
        "R1:1": {"name": ""}, "R1:2": {"name": ""},
        "R2:1": {"name": ""}, "R2:2": {"name": ""},
    }

    result = match_pins(components, pins, [], assigned=set())
    nets = {}
    for entry in result["new_nets"]:
        nets.setdefault(entry["net"], set()).update(entry["pins"])

    assert {"J1:A5", "R1:1"}.issubset(nets["CC1"])
    assert {"J1:B5", "R2:1"}.issubset(nets["CC2"])
    assert "J1:A5" not in nets["GND"]
    assert "J1:B5" not in nets["GND"]
    assert {"R1:2", "R2:2"}.issubset(nets["GND"])


def test_board_model_keeps_power_nets_alongside_signal_nets():
    nets = _build_nets_from_netlist(
        [{"source": "U1:10", "target": "U2:3", "net": "I2C_SDA"}],
        {},
        [{"pin": "U1:1", "net": "3V3"}, {"pin": "U2:1", "net": "3V3"}, {"pin": "U1:2", "net": "GND"}],
    )
    by_name = {entry["name"]: set(entry["pins"]) for entry in nets}

    assert by_name["I2C_SDA"] == {"U1:10", "U2:3"}
    assert by_name["3V3"] == {"U1:1", "U2:1"}
    assert by_name["GND"] == {"U1:2"}


def test_erc_failures_never_proceed_to_pcb_approval():
    assert _route_after_erc({"error": "EV001"}) == "error_end"
    assert _route_after_erc({"_erc_results": {"errors": [{"type": "unknown"}]}}) == "error_end"


def test_error_end_reports_constraint_detail_when_no_error_string_exists():
    result = error_end_node(
        {"fatal_errors": [{"code": "MISSING_POWER_INPUT", "message": "Power input connector is missing"}]},
        {"configurable": {"emit": lambda *_: None}},
    )

    assert result["error"] == "Constraint gate failed: Power input connector is missing"


def test_esp32_c3_native_usb_satisfies_programming_interface_requirement():
    components = [
        {"ref_des": "U1", "id_str": "MCU_Espressif:ESP32-C3-MINI-1"},
        {"ref_des": "J1", "id_str": "Connector:USB_C_Receptacle_USB2.0_16P"},
    ]

    assert _check_missing_programming_header(components, "custom_pcb") == []


def test_cortex_etm_connector_is_not_a_programming_interface_for_esp32_c3():
    components = [
        {"ref_des": "U1", "id_str": "MCU_Espressif:ESP32-C3-MINI-1"},
        {"ref_des": "J2", "id_str": "Connector:Conn_ARM_Cortex_Debug_ETM_20"},
    ]

    errors = _check_missing_programming_header(components, "custom_pcb")

    assert [error["code"] for error in errors] == ["MISSING_PROGRAMMING_HEADER"]


def test_programming_header_repair_never_substitutes_an_unrelated_connector(monkeypatch):
    components = [{"ref_des": "U1", "id_str": "MCU_Microchip_ATmega:ATmega328P"}]
    monkeypatch.setattr(
        "agent.nodes.repair.search_components",
        lambda *_args, **_kwargs: [{"id_str": "Connector:Conn_ARM_Cortex_Debug_ETM_20"}],
    )

    changes = _repair_missing_programming_header({}, components, {"configurable": {}})

    assert changes == []
    assert len(components) == 1


def test_programming_header_repair_satisfies_the_same_constraint(monkeypatch):
    components = [{"ref_des": "U1", "id_str": "MCU_Microchip_ATmega:ATmega328P"}]
    monkeypatch.setattr(
        "agent.nodes.repair.search_components",
        lambda *_args, **_kwargs: [{"id_str": "Connector:Conn_01x04_Pin"}],
    )

    changes = _repair_missing_programming_header({}, components, {"configurable": {}})

    assert changes == ["MISSING_PROGRAMMING_HEADER"]
    assert _check_missing_programming_header(components, "custom_pcb") == []
