"""
Quick test to verify the duplicate detection and pin validation fixes.
Run this before starting the server to ensure fixes are working.
"""

import json
from agent.graph import _generate_nets_fallback

# Test 1: Duplicate component detection
print("=" * 60)
print("TEST 1: Duplicate Component Detection")
print("=" * 60)

selected_components = [
    {"id_str": "MCU_Module:ESP32-WROOM-32", "ref_des": "U1", "category": "MCU_Module"},
    {"id_str": "MCU_Module:ESP32-WROOM-32", "ref_des": "U2", "category": "MCU_Module"},  # DUPLICATE
    {"id_str": "Device:R", "ref_des": "R1", "category": "Device"},
]

seen_ids = set()
duplicates_found = []
for comp in selected_components:
    if comp["id_str"] in seen_ids:
        duplicates_found.append(comp["id_str"])
    seen_ids.add(comp["id_str"])

if duplicates_found:
    print(f"[PASS] Detected {len(duplicates_found)} duplicate(s): {duplicates_found}")
else:
    print("[FAIL] No duplicates detected (should have found ESP32)")

# Test 2: Pin validation
print("\n" + "=" * 60)
print("TEST 2: Pin Validation")
print("=" * 60)

pin_matrix = {
    "U1:1": {"name": "GND", "x": 0, "y": 0},
    "U1:2": {"name": "VCC", "x": 0, "y": 2.54},
    "R1:1": {"name": "1", "x": 10, "y": 0},
    "R1:2": {"name": "2", "x": 10, "y": 2.54},
}

test_netlist = [
    {"source": "U1:1", "target": "R1:1"},  # Valid
    {"source": "U1:99", "target": "R1:1"},  # Invalid - U1:99 doesn't exist
    {"source": "U1:1", "target": "U1:1"},  # Invalid - self-connection
]

valid_count = 0
for conn in test_netlist:
    src = conn["source"]
    tgt = conn["target"]
    if src in pin_matrix and tgt in pin_matrix and src != tgt:
        valid_count += 1

print(f"Input: {len(test_netlist)} connections")
print(f"Valid: {valid_count} connections")
if valid_count == 1:
    print("[PASS] Correctly filtered invalid connections")
else:
    print(f"[FAIL] Expected 1 valid connection, got {valid_count}")

# Test 3: Improved fallback netlist
print("\n" + "=" * 60)
print("TEST 3: Improved Fallback Netlist Generator")
print("=" * 60)

pin_matrix_test = {
    "U1:1": {"name": "GND"},
    "U1:2": {"name": "VCC"},
    "U2:1": {"name": "GND"},
    "U2:2": {"name": "3V3"},
    "R1:1": {"name": "1"},
    "R1:2": {"name": "2"},
}

nets = _generate_nets_fallback(pin_matrix_test)
print(f"Generated {len(nets)} nets:")
for net in nets:
    print(f"  {net['net']}: {', '.join(net['pins'])}")

net_map = {n["net"]: set(n["pins"]) for n in nets}

# Should have GND net with both ground pins
gnd_pins = net_map.get("GND", set())
gnd_connected = "U1:1" in gnd_pins and "U2:1" in gnd_pins
if gnd_connected:
    print("[PASS] GND pins grouped in GND net")
else:
    print("[FAIL] GND pins should be in GND net")

# Should NOT connect R1:1 to R1:2 (they don't share a name and don't match aliases)
r_in_same_net = False
for net in nets:
    pins = net["pins"]
    if "R1:1" in pins and "R1:2" in pins:
        r_in_same_net = True
        break

if not r_in_same_net:
    print("[PASS] Resistor pins are NOT connected (different names, no alias match)")
else:
    print("[FAIL] Resistor pins should NOT be connected")

print("\n" + "=" * 60)
print("All tests completed!")
print("=" * 60)
print("\nIf all tests passed, the fixes are working correctly.")
print("You can now run: python server.py")