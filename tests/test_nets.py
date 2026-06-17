"""Test the net-based netlist pipeline: power/GND separation,
passive duplication rules, ref prefix fixing, and export with
global labels + junctions."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from agent.graph import (
    _generate_nets_fallback, _is_gnd_net, _is_power_net,
    _is_passive, _ref_prefix_for,
)
from agent.kicad_export import generate_kicad_sch

passed = failed = 0
def check(label, cond):
    global passed, failed
    print(f"  {'[PASS]' if cond else '[FAIL]'} {label}")
    passed += cond
    failed += not cond

print("=" * 60)
print("TEST 1: Net classification")
print("=" * 60)
check("GND detected", _is_gnd_net("GND") and _is_gnd_net("VSS") and _is_gnd_net("AGND"))
check("Power detected", _is_power_net("3V3") and _is_power_net("VCC") and _is_power_net("+5V") and _is_power_net("VBAT"))
check("Signals NOT power", not _is_power_net("SDA") and not _is_gnd_net("GPIO4") and not _is_power_net("XTAL1"))

print()
print("=" * 60)
print("TEST 2: Passive duplication & ref prefixes")
print("=" * 60)
check("Device:C is passive (multiple allowed)", _is_passive("Device:C", "Device"))
check("MCU is NOT passive (unique)", not _is_passive("MCU_Module:ESP32-WROOM-32", "MCU_Module"))
check("Capacitor prefix = C", _ref_prefix_for("Device:C", "Device") == "C")
check("Resistor prefix = R", _ref_prefix_for("Device:R", "Device") == "R")
check("Crystal prefix = Y", _ref_prefix_for("Device:Crystal", "Device") == "Y")
check("MCU prefix = U (no more C3 microcontrollers)", _ref_prefix_for("MCU_Module:ESP32-WROOM-32", "MCU_Module") == "U")

print()
print("=" * 60)
print("TEST 3: Fallback nets - GND isolated from power and signals")
print("=" * 60)
pin_matrix = {
    "U1:1": {"name": "VDD"}, "U1:2": {"name": "GND"}, "U1:3": {"name": "SDA"}, "U1:4": {"name": "SCL"},
    "U2:1": {"name": "VCC"}, "U2:2": {"name": "VSS"}, "U2:3": {"name": "SDA"}, "U2:4": {"name": "SCL"},
    "C1:1": {"name": "~"}, "C1:2": {"name": "~"},
}
nets = _generate_nets_fallback(pin_matrix)
net_map = {n["net"]: set(n["pins"]) for n in nets}
check("GND net exists with both ground pins", net_map.get("GND") == {"U1:2", "U2:2"})
check("Power net groups VDD+VCC", "U1:1" in net_map.get("3V3", set()) and "U2:1" in net_map.get("3V3", set()))
check("GND does not contain power pins", not (net_map.get("GND", set()) & {"U1:1", "U2:1"})),
check("SDA net pairs the two SDA pins", net_map.get("SDA") == {"U1:3", "U2:3"})
check("No net mixes GND with signals", "U1:3" not in net_map.get("GND", set()))

print()
print("=" * 60)
print("TEST 4: Export with power labels + junctions")
print("=" * 60)
mcu_ops = [
    ['rectangle', ['start', '-7.62', '10.16'], ['end', '7.62', '-10.16'],
     ['stroke', ['width', '0.254']], ['fill', ['type', 'background']]],
    ['pin', 'power_in', 'line', ['at', '-10.16', '5.08', '0'], ['length', '2.54'],
     ['name', 'VCC', ['effects']], ['number', '1', ['effects']]],
    ['pin', 'power_in', 'line', ['at', '-10.16', '-5.08', '0'], ['length', '2.54'],
     ['name', 'GND', ['effects']], ['number', '2', ['effects']]],
]
design = {
    'selected_components': [
        {'id_str': 'MCU_Module:TestMCU', 'ref_des': 'U1', 'category': 'MCU_Module', 'description': ''},
    ],
    'component_ops': {'U1': mcu_ops},
    'component_placements': [{'ref_des': 'U1', 'x': 0, 'y': 0}],
    'wire_paths': [
        # Three wires meeting at (12.7, 0) -> junction expected
        {'source': 'A', 'target': 'B', 'path': [{'x': 0, 'y': 0}, {'x': 12.7, 'y': 0}]},
        {'source': 'B', 'target': 'C', 'path': [{'x': 12.7, 'y': 0}, {'x': 25.4, 'y': 0}]},
        {'source': 'B', 'target': 'D', 'path': [{'x': 12.7, 'y': 0}, {'x': 12.7, 'y': 12.7}]},
    ],
    'power_labels': [
        {'pin': 'U1:2', 'net': 'GND', 'x': -7.62, 'y': -5.08, 'dir': 'left'},
        {'pin': 'U1:1', 'net': '3V3', 'x': -7.62, 'y': 5.08, 'dir': 'left'},
    ],
}
sch = generate_kicad_sch(design)
check("GND global label emitted", '(global_label "GND"' in sch)
check("3V3 global label emitted", '(global_label "3V3"' in sch)
check("Junction dot emitted at 3-way meet", '(junction (at' in sch)
check("Wires emitted", sch.count('(wire (pts') == 3)

# Balanced parens
depth, in_str, prev = 0, False, ''
for ch in sch:
    if ch == '"' and prev != '\\':
        in_str = not in_str
    elif not in_str:
        depth += (ch == '(') - (ch == ')')
    prev = ch
check("Balanced parentheses", depth == 0)

if __name__ == "__main__":
    print()
    print("=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    sys.exit(1 if failed else 0)