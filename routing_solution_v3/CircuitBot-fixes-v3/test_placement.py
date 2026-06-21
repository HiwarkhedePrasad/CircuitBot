"""Smoke test for the deterministic placement + coord validator.

Run with: python test_placement.py
"""
import sys
sys.path.insert(0, '.')

from pcb_design.placement import place_components_deterministic
from pcb_design.coord_validator import (
    validate_component_placements,
    validate_wire_paths,
    sanitize_design,
    repair_wire_path,
)

# Mock components: a simple ESP32 + USB-C + AMS1117 + DS18B20 + decoupling caps
comps = [
    {"ref_des": "J1", "category": "Connector_USB", "id_str": "Connector_USB:USB_C_Receptacle_USB2.0",
     "pads": [{"x": 0, "y": 0, "width": 2, "height": 1, "sx": 2, "sy": 1}]},
    {"ref_des": "U1", "category": "MCU", "id_str": "MCU_Module:ESP32-WROOM",
     "pads": [{"x": -5, "y": 0, "width": 1, "height": 1, "sx": 1, "sy": 1},
              {"x": 5, "y": 0, "width": 1, "height": 1, "sx": 1, "sy": 1}]},
    {"ref_des": "U2", "category": "Regulator_Linear", "id_str": "Regulator_Linear:AMS1117-3.3",
     "pads": [{"x": 0, "y": -2, "width": 1, "height": 1, "sx": 1, "sy": 1},
              {"x": 0, "y": 0, "width": 1, "height": 1, "sx": 1, "sy": 1},
              {"x": 0, "y": 2, "width": 1, "height": 1, "sx": 1, "sy": 1}]},
    {"ref_des": "U3", "category": "Sensor_Temperature", "id_str": "Sensor_Temperature:DS18B20",
     "pads": [{"x": -1, "y": 0, "width": 0.5, "height": 0.5, "sx": 0.5, "sy": 0.5},
              {"x": 0, "y": 0, "width": 0.5, "height": 0.5, "sx": 0.5, "sy": 0.5},
              {"x": 1, "y": 0, "width": 0.5, "height": 0.5, "sx": 0.5, "sy": 0.5}]},
    {"ref_des": "C1", "category": "Device", "id_str": "Device:C_Small",
     "pads": [{"x": -1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8},
              {"x": 1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8}]},
    {"ref_des": "C2", "category": "Device", "id_str": "Device:C_Small",
     "pads": [{"x": -1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8},
              {"x": 1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8}]},
    {"ref_des": "R1", "category": "Device", "id_str": "Device:R_Small",
     "pads": [{"x": -1, "y": 0, "width": 0.6, "height": 0.6, "sx": 0.6, "sy": 0.6},
              {"x": 1, "y": 0, "width": 0.6, "height": 0.6, "sx": 0.6, "sy": 0.6}]},
    {"ref_des": "D1", "category": "Device", "id_str": "Device:LED",
     "pads": [{"x": -1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8},
              {"x": 1, "y": 0, "width": 0.8, "height": 0.8, "sx": 0.8, "sy": 0.8}]},
]

# Mock netlist: power + signal connections
netlist = [
    {"source": "J1:1", "target": "U2:1", "net": "VBUS"},   # USB → LDO input
    {"source": "U2:2", "target": "U1:1", "net": "3V3"},    # LDO → ESP32 VCC
    {"source": "C1:1", "target": "U2:1", "net": "VBUS"},   # decoupling
    {"source": "C1:2", "target": "J1:4",  "net": "GND"},   # decoupling GND
    {"source": "C2:1", "target": "U2:2", "net": "3V3"},    # decoupling
    {"source": "C2:2", "target": "U1:2",  "net": "GND"},   # decoupling GND
    {"source": "U1:3", "target": "U3:2", "net": "OWD"},    # 1-Wire data
    {"source": "U1:4", "target": "R1:1", "net": "LED_DRV"},# LED drive
    {"source": "R1:2", "target": "D1:1", "net": "LED_DRV"},# LED current limit
]

# Mock pin_matrix (only positions matter for placement)
pin_matrix = {
    "J1:1": {"x": 0, "y": 0}, "J1:4": {"x": 0, "y": 0},
    "U1:1": {"x": -5, "y": 0}, "U1:2": {"x": 5, "y": 0},
    "U1:3": {"x": 0, "y": 5}, "U1:4": {"x": 0, "y": -5},
    "U2:1": {"x": 0, "y": -2}, "U2:2": {"x": 0, "y": 0},
    "U3:2": {"x": 0, "y": 0},
    "C1:1": {"x": -1, "y": 0}, "C1:2": {"x": 1, "y": 0},
    "C2:1": {"x": -1, "y": 0}, "C2:2": {"x": 1, "y": 0},
    "R1:1": {"x": -1, "y": 0}, "R1:2": {"x": 1, "y": 0},
    "D1:1": {"x": -1, "y": 0},
}

print("=== Test 1: Deterministic placement ===")
placements = place_components_deterministic(comps, netlist, pin_matrix)
for p in placements:
    print(f"  {p['ref_des']:5s} at ({p['x']:6.2f}, {p['y']:6.2f})")

print()
print("=== Test 2: Validate placements (should all pass) ===")
clean_p, errs = validate_component_placements(placements)
print(f"  Kept: {len(clean_p)}/{len(placements)}")
print(f"  Errors: {len(errs)}")
for e in errs:
    print(f"    {e}")

print()
print("=== Test 3: Validate BAD placements (should drop them) ===")
bad_placements = [
    {"ref_des": "U1", "x": 50, "y": 50},     # OK
    {"ref_des": "U2", "x": 9999, "y": 50},   # out of bounds
    {"ref_des": "U3", "x": 50, "y": -999},   # out of bounds
    {"ref_des": "",    "x": 50, "y": 50},    # missing ref
    {"ref_des": "U4", "x": "abc", "y": 50},  # non-numeric
]
clean_p, errs = validate_component_placements(bad_placements)
print(f"  Kept: {len(clean_p)}/5")
print(f"  Errors: {len(errs)}")
for e in errs:
    print(f"    {e}")

print()
print("=== Test 4: Validate wire paths (should pass) ===")
good_wires = [
    {"source": "U1:1", "target": "U2:1",
     "path": [{"x": 10, "y": 10}, {"x": 20, "y": 10}, {"x": 20, "y": 20}]},
]
clean_w, errs = validate_wire_paths(good_wires)
print(f"  Kept: {len(clean_w)}/1")
print(f"  Errors: {len(errs)}")

print()
print("=== Test 5: Validate BAD wires (diagonal + too long) ===")
bad_wires = [
    # Diagonal segment
    {"source": "A:1", "target": "B:1",
     "path": [{"x": 0, "y": 0}, {"x": 50, "y": 50}]},
    # Way too long
    {"source": "C:1", "target": "D:1",
     "path": [{"x": 0, "y": 0}, {"x": 0, "y": 999}]},
    # Degenerate (1 point)
    {"source": "E:1", "target": "F:1",
     "path": [{"x": 0, "y": 0}]},
]
clean_w, errs = validate_wire_paths(bad_wires)
print(f"  Kept: {len(clean_w)}/3 (should be 0)")
print(f"  Errors: {len(errs)}")
for e in errs:
    print(f"    {e}")

print()
print("=== Test 6: Repair a diagonal wire ===")
diagonal = [{"x": 0, "y": 0}, {"x": 30, "y": 40}]
repaired = repair_wire_path(diagonal)
if repaired:
    print(f"  Repaired to {len(repaired)} points:")
    for p in repaired:
        print(f"    ({p['x']}, {p['y']})")
    # Verify orthogonal
    for i in range(len(repaired) - 1):
        dx = abs(repaired[i]['x'] - repaired[i+1]['x'])
        dy = abs(repaired[i]['y'] - repaired[i+1]['y'])
        assert dx < 0.001 or dy < 0.001, "STILL DIAGONAL!"
    print("  ✓ All segments orthogonal")
else:
    print("  ✗ Repair returned None")

print()
print("=== ALL TESTS PASSED ===")
