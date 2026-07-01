"""Tests for the MST ratsnest generator (Ticket 3)."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pcb_design.board_model import BoardModel, BoardComponent, PadDef, BoardTrace
from pcb_design.ratsnest import compute_ratsnest

passed = failed = 0
def check(label, cond):
    global passed, failed
    print(f"  {'[PASS]' if cond else '[FAIL]'} {label}")
    passed += cond
    failed += not cond

print("=" * 60)
print("RATSNEST TEST 1: 4-pad net, no traces -> 3 MST edges")
print("=" * 60)
model = BoardModel()
for i, label in enumerate(["R1", "R2", "R3", "R4"]):
    model.components.append(BoardComponent(
        ref=label, footprint="R_0805",
        x=float(i * 10), y=0.0,
        pads=[PadDef(number="1", x=0, y=0, width=1, height=1)],
    ))
model.nets = [{"name": "NET_A", "pins": ["R1:1", "R2:1", "R3:1", "R4:1"]}]
result = compute_ratsnest(model)
check("NET_A in result", "NET_A" in result)
edges = result.get("NET_A", [])
check("3 MST edges", len(edges) == 3)

print()
print("=" * 60)
print("RATSNEST TEST 2: Same net with trace connecting R1-R2")
print("=" * 60)
model2 = BoardModel()
for i, label in enumerate(["R1", "R2", "R3", "R4"]):
    model2.components.append(BoardComponent(
        ref=label, footprint="R_0805",
        x=float(i * 10), y=0.0,
        pads=[PadDef(number="1", x=0, y=0, width=1, height=1)],
    ))
model2.nets = [{"name": "NET_A", "pins": ["R1:1", "R2:1", "R3:1", "R4:1"]}]
model2.traces.append(BoardTrace(net="NET_A", layer="F.Cu", width=0.254,
    path=[(0.0, 0.0), (10.0, 0.0)]))
result2 = compute_ratsnest(model2)
check("NET_A still in result", "NET_A" in result2)
edges2 = result2.get("NET_A", [])
check("2 edges after routing one pair", len(edges2) == 2)

print()
print("=" * 60)
print("RATSNEST TEST 3: Single-pin net -> no edges")
print("=" * 60)
model3 = BoardModel()
model3.components.append(BoardComponent(
    ref="J1", footprint="Connector", x=0, y=0,
    pads=[PadDef(number="1", x=0, y=0, width=1, height=1)],
))
model3.nets = [{"name": "NET_SINGLE", "pins": ["J1:1"]}]
result3 = compute_ratsnest(model3)
check("Single-pin net not in result", "NET_SINGLE" not in result3)

print()
print("=" * 60)
print("RATSNEST TEST 4: Empty pin list -> no edges")
print("=" * 60)
model4 = BoardModel()
model4.nets = [{"name": "EMPTY", "pins": []}]
result4 = compute_ratsnest(model4)
check("Empty net not in result", "EMPTY" not in result4)

print()
print("=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)
sys.exit(1 if failed else 0)
