"""Tests for pipeline component retrieval and reference designator prefix fixes."""

import pytest
from agent.nodes.repair import _next_ref, repair_node
from agent.templates.matcher import get_library_filter
from agent.utils import _ref_prefix_for


def test_ref_prefix_for_capacitors_and_resistors():
    assert _ref_prefix_for("Device:C", "Device") == "C"
    assert _ref_prefix_for("Device:C_US", "Device") == "C"
    assert _ref_prefix_for("Device:CP", "Device") == "C"
    assert _ref_prefix_for("Device:R", "Device") == "R"
    assert _ref_prefix_for("Device:R_US", "Device") == "R"
    assert _ref_prefix_for("Device:D", "Device") == "D"
    assert _ref_prefix_for("Device:LED", "Device") == "D"
    assert _ref_prefix_for("Connector_USB:USB_C_Receptacle_USB20", "Connector") == "J"
    assert _ref_prefix_for("Regulator_Linear:AMS1117-3.3", "Regulator") == "U"


def test_next_ref_generation():
    comps = [
        {"ref_des": "C1", "id_str": "Device:C"},
        {"ref_des": "R1", "id_str": "Device:R"},
    ]
    assert _next_ref("C", comps) == "C2"
    assert _next_ref("R", comps) == "R2"
    assert _next_ref("U", comps) == "U1"


def test_repair_node_corrects_mismatched_ref_prefix():
    state = {
        "selected_components": [
            {"ref_des": "U1", "id_str": "MCU_Espressif:ESP32-C3", "category": "MCU_Espressif"},
            {"ref_des": "R2", "id_str": "Device:C_US", "category": "Device"},  # Mismatched! Capacitor keyed as R2
        ],
        "repairable_errors": [],
        "repair_passes_used": 0,
    }

    result = repair_node(state, config={"configurable": {}})
    selected = result.get("selected_components") or result.get("state_update", {}).get("selected_components", [])

    cap = next(c for c in selected if c["id_str"] == "Device:C_US")
    assert cap["ref_des"].startswith("C"), f"Expected capacitor prefix 'C', got '{cap['ref_des']}'"


def test_get_library_filter_for_subsystems():
    assert get_library_filter({"subsystem": "Power Input"}) == "Connector|Connector_USB"
    assert get_library_filter({"subsystem": "Power Regulation"}) == "Regulator_Linear|Regulator_Switching|Regulator_Controller|Regulator_Current|Regulator_SwitchedCapacitor"
    assert get_library_filter({"subsystem": "USB-UART Bridge"}) == "Interface_USB|Connector_USB"
    assert get_library_filter({"subsystem": "Digital Temperature Sensing"}) == "Sensor_Temperature|Sensor"
