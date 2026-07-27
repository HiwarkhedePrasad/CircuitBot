import pytest
from agent.pin_matcher import match_pins
from agent.nodes.deduplicator import deduplicator_node

def test_vbus_not_merged_into_vdd():
    """Verify USB VBUS pin does not merge into VDD 3.3V rail."""
    components = [
        {"ref_des": "U1", "id_str": "MCU_Module:ESP32-C3-MINI-1"},
        {"ref_des": "J1", "id_str": "Connector:USB_C_Receptacle_USB2.0_16P"},
    ]
    pin_matrix = {
        "U1:1": {"name": "VDD", "type": "power_in"},
        "U1:2": {"name": "GND", "type": "power_in"},
        "J1:VBUS": {"name": "VBUS", "type": "power_out"},
        "J1:GND": {"name": "GND", "type": "power_out"},
    }
    existing_power_pins = [
        {"pin": "U1:1", "net": "VDD"},
        {"pin": "U1:2", "net": "GND"},
    ]
    
    result = match_pins(components, pin_matrix, [], existing_power_pins)
    
    # Check that J1:VBUS is assigned to VBUS or 5V, NOT VDD
    vbus_nets = [n for n in result.get("new_nets", []) if "J1:VBUS" in n["pins"]]
    assert len(vbus_nets) > 0, "J1:VBUS should be matched to a power rail"
    assert vbus_nets[0]["net"] != "VDD", "J1:VBUS must not merge into VDD"
    assert vbus_nets[0]["net"] in ("VBUS", "5V", "VUSB", "VIN")

def test_sensor_add0_mapped_to_gnd():
    """Verify TMP117 / sensor ADD0 address pin maps to GND by default."""
    components = [
        {"ref_des": "U2", "id_str": "Sensor_Temperature:TMP117xxYBG"},
    ]
    pin_matrix = {
        "U2:1": {"name": "SCL", "type": "input"},
        "U2:2": {"name": "SDA", "type": "bidirectional"},
        "U2:3": {"name": "ADD0", "type": "input"},
        "U2:4": {"name": "VDD", "type": "power_in"},
        "U2:5": {"name": "GND", "type": "power_in"},
    }
    existing_power_pins = [
        {"pin": "U2:4", "net": "VDD"},
        {"pin": "U2:5", "net": "GND"},
    ]
    
    result = match_pins(components, pin_matrix, [], existing_power_pins)
    add0_nets = [n for n in result.get("new_nets", []) if "U2:3" in n["pins"]]
    assert len(add0_nets) > 0, "U2:ADD0 pin should be matched"
    assert add0_nets[0]["net"] == "GND", "ADD0 pin must map to GND for default 0x48 I2C address"

def test_passive_deduplication_preserves_multiple_caps():
    """Verify deduplicator node preserves multiple passive decoupling caps."""
    state = {
        "selected_components": [
            {"ref_des": "U1", "id_str": "Regulator_Linear:AMS1117-3.3", "subsystem": "Power Regulation", "category": "Regulator_Linear"},
            {"ref_des": "C1", "id_str": "Device:C_Small", "subsystem": "Power Regulation", "category": "Device"},
            {"ref_des": "C2", "id_str": "Device:C_Small", "subsystem": "Power Regulation", "category": "Device"},
            {"ref_des": "C3", "id_str": "Device:C_Small", "subsystem": "Power Regulation", "category": "Device"},
        ]
    }
    
    config = {"configurable": {}}
    res = deduplicator_node(state, config)
    remaining = res["selected_components"]
    ref_des_list = [c["ref_des"] for c in remaining]
    
    assert "C1" in ref_des_list
    assert "C2" in ref_des_list
    assert "C3" in ref_des_list
    assert len(remaining) == 4, "All decoupling capacitors should be retained"
