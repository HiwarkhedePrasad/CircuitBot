"""Test routing fixes (no wire-through-component, no wire overlap)
and the .kicad_sch exporter."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from agent.layout_engine import BackendLayoutEngine, GRID_SIZE, MATRIX_OFFSET
from agent.kicad_export import generate_kicad_sch, _simplify_path

print("=" * 60)
print("TEST 1: Routing - wires must not cross component bodies")
print("=" * 60)

# Polyline-bodied component (like a resistor symbol) - previously NOT blocked
res_ops = [
    ['polyline', ['pts', ['xy', '-2.54', '1.27'], ['xy', '2.54', '1.27'],
                  ['xy', '2.54', '-1.27'], ['xy', '-2.54', '-1.27'], ['xy', '-2.54', '1.27']],
     ['stroke', ['width', '0.254']], ['fill', ['type', 'none']]],
    ['pin', 'passive', 'line', ['at', '-5.08', '0', '0'], ['length', '2.54'],
     ['name', '~', ['effects']], ['number', '1', ['effects']]],
    ['pin', 'passive', 'line', ['at', '5.08', '0', '180'], ['length', '2.54'],
     ['name', '~', ['effects']], ['number', '2', ['effects']]],
]

# Big rectangle-bodied MCU
mcu_ops = [
    ['rectangle', ['start', '-7.62', '10.16'], ['end', '7.62', '-10.16'],
     ['stroke', ['width', '0.254']], ['fill', ['type', 'background']]],
    ['pin', 'power_in', 'line', ['at', '-10.16', '5.08', '0'], ['length', '2.54'],
     ['name', 'VCC', ['effects']], ['number', '1', ['effects']]],
    ['pin', 'power_in', 'line', ['at', '-10.16', '-5.08', '0'], ['length', '2.54'],
     ['name', 'GND', ['effects']], ['number', '2', ['effects']]],
    ['pin', 'input', 'line', ['at', '10.16', '0', '180'], ['length', '2.54'],
     ['name', 'IO1', ['effects']], ['number', '3', ['effects']]],
]

engine = BackendLayoutEngine()
engine.add_component('U1', mcu_ops, 'MCU_Module')
engine.add_component('R1', res_ops, 'Device')
engine.execute_placement()

# Build pin matrix (relative pin endpoint coordinates, same as graph.py logic)
pin_matrix = {
    'U1:1': {'x': -7.62, 'y': 5.08, 'name': 'VCC'},
    'U1:2': {'x': -7.62, 'y': -5.08, 'name': 'GND'},
    'U1:3': {'x': 7.62, 'y': 0.0, 'name': 'IO1'},
    'R1:1': {'x': -2.54, 'y': 0.0, 'name': '~'},
    'R1:2': {'x': 2.54, 'y': 0.0, 'name': '~'},
}

engine.build_obstacle_matrix(pin_matrix=pin_matrix)

# Verify polyline component body cells are blocked now
r1 = engine._get_comp('R1')
body_cx = round((r1['x']) / GRID_SIZE) + MATRIX_OFFSET
body_cy = round((r1['y']) / GRID_SIZE) + MATRIX_OFFSET
center_blocked = engine.matrix[body_cy][body_cx] == 0
print(f"R1 (polyline body) center cell blocked: {center_blocked}")
print("[PASS]" if center_blocked else "[FAIL]", "- polyline components are now obstacles")

netlist = [
    {'source': 'U1:3', 'target': 'R1:1'},
    {'source': 'U1:1', 'target': 'R1:2'},
    {'source': 'U1:2', 'target': 'R1:2'},
]
traces = engine.route_traces(netlist, pin_matrix)
print(f"Routed {len(traces)}/{len(netlist)} traces")

# Check no trace cell sits inside a component body (excluding pin corridors)
violations = 0
for t in traces:
    for pt in t['path'][1:-1]:
        gx = round(pt['x'] / GRID_SIZE) + MATRIX_OFFSET
        gy = round(pt['y'] / GRID_SIZE) + MATRIX_OFFSET
        if engine.matrix[gy][gx] == 0:
            violations += 1
print(f"Trace cells inside blocked areas: {violations}")
print("[PASS]" if violations == 0 else "[FAIL]", "- no wires through components")

# Check overlap between traces (shared non-endpoint cells)
cell_usage = {}
overlaps = 0
for t in traces:
    for pt in t['path'][2:-2]:
        key = (round(pt['x'] / GRID_SIZE), round(pt['y'] / GRID_SIZE))
        if key in cell_usage and cell_usage[key] != id(t):
            overlaps += 1
        cell_usage[key] = id(t)
print(f"Overlapping wire cells: {overlaps}")
print("[PASS]" if overlaps <= 2 else "[WARN]", "- wire overlap minimized (crossings allowed)")

print()
print("=" * 60)
print("TEST 2: .kicad_sch export")
print("=" * 60)

design = {
    'selected_components': [
        {'id_str': 'MCU_Module:TestMCU', 'ref_des': 'U1', 'category': 'MCU_Module', 'description': 'MCU'},
        {'id_str': 'Device:R', 'ref_des': 'R1', 'category': 'Device', 'description': 'Resistor'},
    ],
    'component_ops': {'U1': mcu_ops, 'R1': res_ops},
    'component_placements': engine.get_placements(),
    'wire_paths': traces,
}

sch_text = generate_kicad_sch(design)

checks = [
    ('(kicad_sch', 'header'),
    ('(lib_symbols', 'lib_symbols section'),
    ('"MCU_Module:TestMCU"', 'lib_id for MCU'),
    ('"Device:R"', 'lib_id for resistor'),
    ('(property "Reference" "U1"', 'reference property'),
    ('(wire (pts', 'wire segments'),
    ('(sheet_instances', 'sheet instances'),
    ('(instances', 'symbol instances'),
]
all_ok = True
for needle, label in checks:
    ok = needle in sch_text
    all_ok &= ok
    print(f"  {'[PASS]' if ok else '[FAIL]'} {label}")

# Balanced parentheses sanity check
depth = 0
in_str = False
prev = ''
for ch in sch_text:
    if ch == '"' and prev != '\\':
        in_str = not in_str
    elif not in_str:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
    prev = ch
print(f"  {'[PASS]' if depth == 0 else '[FAIL]'} balanced parentheses (depth={depth})")

# Path simplification
path = [{'x': 0, 'y': 0}, {'x': 1.27, 'y': 0}, {'x': 2.54, 'y': 0},
        {'x': 2.54, 'y': 1.27}, {'x': 2.54, 'y': 2.54}]
simp = _simplify_path(path)
print(f"  {'[PASS]' if len(simp) == 3 else '[FAIL]'} path simplification: {len(path)} pts -> {len(simp)} pts")

# Write sample file
with open('test_export.kicad_sch', 'w', encoding='utf-8') as f:
    f.write(sch_text)
print("\nSample schematic written to test_export.kicad_sch")
print(f"File size: {len(sch_text)} chars, {sch_text.count(chr(10))} lines")
print("\nAll tests completed!" if all_ok else "\nSome tests FAILED - review above")