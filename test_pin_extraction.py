"""Unit test for the derived-symbol pin parser bug (the "0 pins" bug).

Derived KiCad symbols like Power_Management:LTC4417HUF contain only
properties plus an (extends "ParentName") clause — all pins live in the
parent symbol (LTC4417CUF). The parser must follow the extends chain or
the LLM is fed components with 0 pins and can never wire them.
"""

import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from agent.graph import _parse_sexpr_to_ops, _extract_pins_from_ops
from agent.tools import fetch_sexpr

passed = failed = 0
def check(label, cond):
    global passed, failed
    print(f"  {'[PASS]' if cond else '[FAIL]'} {label}")
    passed += cond
    failed += not cond


def load_pins(id_str, ref_des):
    sexpr = fetch_sexpr(id_str)
    ops = _parse_sexpr_to_ops(sexpr, id_str.split(":")[0])
    return ops, _extract_pins_from_ops(ops, ref_des)


print("=" * 60)
print("TEST 1: Derived symbol Power_Management:LTC4417HUF (extends LTC4417CUF)")
print("=" * 60)
ops, pins = load_pins("Power_Management:LTC4417HUF", "U1")
pin_ops = [op for op in ops if op[0] == "pin"]
names = {p["name"].upper() for p in pins.values()}

check("Parser followed (extends) and found pin ops", len(pin_ops) > 0)
check(f"Extracted 25 pins (24 + EP), got {len(pins)}", len(pins) == 25)
check("GND pin present", "GND" in names)
check("Power input pins present (V1/V2/V3)", {"V1", "V2", "V3"} <= names)
check("Every pin key is namespaced to U1", all(k.startswith("U1:") for k in pins))
check("Every pin has a non-empty pin number", all(p["pin_num"] for p in pins.values()))
check("No 'extends' op leaks into the ops list consumed downstream",
      all(op[0] != "extends" for op in ops))

print()
print("=" * 60)
print("TEST 2: Derived symbol Power_Supervisor:MCP130-xxxDxTO (extends MCP120-xxxDxTO)")
print("=" * 60)
ops2, pins2 = load_pins("Power_Supervisor:MCP130-xxxDxTO", "U4")
check(f"Extracted pins (got {len(pins2)})", len(pins2) >= 3)
check("All pins namespaced to U4", all(k.startswith("U4:") for k in pins2))

print()
print("=" * 60)
print("TEST 3: Non-derived symbol still parses (regression guard)")
print("=" * 60)
ops3, pins3 = load_pins("Power_Management:LTC4417CUF", "U9")
check(f"Base symbol LTC4417CUF has 25 pins, got {len(pins3)}", len(pins3) == 25)
check("Derived symbol pin set matches its parent's pin numbers",
      {p["pin_num"] for p in pins.values()} == {p["pin_num"] for p in pins3.values()})

print()
print(f"{'=' * 60}\nRESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)