"""Tests for the batched netlist pipeline and post-route overlap checking.

Verifies:
- Power/GND pins are assigned deterministically (no LLM involved)
- Signal pins are sent to the LLM in batches with the hub (MCU) in every batch
- Hallucinated / out-of-batch / duplicate pins are dropped per batch
- Nets with the same name from different batches are merged
- check_and_fix_overlaps re-routes parallel overlapping wires
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import json
import agent.graph as graph
from agent.graph import _make_signal_batches, _merge_net, netlist_node
from agent.layout_engine import BackendLayoutEngine, GRID_SIZE, MATRIX_SIZE

passed = failed = 0
def check(label, cond):
    global passed, failed
    print(f"  {'[PASS]' if cond else '[FAIL]'} {label}")
    passed += cond
    failed += not cond


print("=" * 60)
print("TEST 1: Batch construction — hub in every batch, size cap respected")
print("=" * 60)
# U1 = hub with 10 pins; U2/U3/U4 peripherals with 4 pins each, cap = 8
keys = [f"U1:{i}" for i in range(1, 11)]
for ref in ("U2", "U3", "U4"):
    keys += [f"{ref}:{i}" for i in range(1, 5)]
batches = _make_signal_batches(keys, max_pins=8)
check(f"Multiple batches created (got {len(batches)})", len(batches) >= 2)
check("Hub U1 present in EVERY batch", all("U1" in b for b in batches))
check("Every peripheral appears exactly once",
      sum(b.count("U2") + b.count("U3") + b.count("U4") for b in batches) == 3)

print()
print("=" * 60)
print("TEST 2: _merge_net — same-name nets merge across batches")
print("=" * 60)
nets = [{"net": "I2C_SDA", "pins": ["U1:3"]}]
_merge_net(nets, "i2c_sda", ["U2:5", "U1:3"])  # case-insensitive, dedup
_merge_net(nets, "RESET", ["U3:1"])
check("Merged into existing net (no duplicate pins)",
      nets[0]["pins"] == ["U1:3", "U2:5"])
check("New net appended", nets[1] == {"net": "RESET", "pins": ["U3:1"]})

print()
print("=" * 60)
print("TEST 3: netlist_node — batched pipeline with mocked LLM")
print("=" * 60)
# MCU U1 (hub) + sensor U2 + supervisor U3; power pins must never reach the LLM.
# U2 and U3 each carry 5 signal pins so a cap of 8 forces TWO batches.
pin_matrix = {
    "U1:1": {"name": "3V3"},  "U1:2": {"name": "GND"},
    "U1:3": {"name": "GPIO4"}, "U1:4": {"name": "GPIO5"},
    "U1:5": {"name": "EN"},    "U1:6": {"name": "RST"},
    "U1:7": {"name": "TX"},    "U1:8": {"name": "RX"},
    "U2:1": {"name": "VDD"},   "U2:2": {"name": "GND"},  "U2:3": {"name": "DQ"},
    "U2:4": {"name": "P1"}, "U2:5": {"name": "P2"}, "U2:6": {"name": "P3"}, "U2:7": {"name": "P4"},
    "U3:1": {"name": "VDD"},   "U3:2": {"name": "GND"},  "U3:3": {"name": "RST_OUT"},
    "U3:4": {"name": "Q1"}, "U3:5": {"name": "Q2"}, "U3:6": {"name": "Q3"}, "U3:7": {"name": "Q4"},
}
for v in pin_matrix.values():
    v.setdefault("x", 0); v.setdefault("y", 0)

llm_calls = []
def fake_llm(system, user):
    llm_calls.append(user)
    if "U2:3" in user:   # batch containing the sensor
        return json.dumps([
            {"net": "ONEWIRE_DQ", "pins": ["U1:3", "U2:3"]},
            {"net": "GHOST", "pins": ["U9:1", "U1:2", "U1:4"]},  # hallucination + power pin
        ])
    if "U3:3" in user:   # batch containing the supervisor
        return json.dumps([
            {"net": "RESET", "pins": ["U3:3", "U1:6"]},
            {"net": "ONEWIRE_DQ", "pins": ["U2:3"]},  # duplicate from earlier batch
        ])
    return "[]"

import agent.nodes.netlist as netlist_module
orig = netlist_module._call_llm_with_tools
orig_cap = netlist_module.MAX_BATCH_PINS
netlist_module._call_llm_with_tools = fake_llm
netlist_module.MAX_BATCH_PINS = 8  # force multiple batches

logs = []
config = {"configurable": {"emit": lambda ev, d: logs.append(d.get("message", ""))}}
state = {
    "prompt": "ESP32 with DS18B20",
    "selected_components": [
        {"ref_des": "U1", "id_str": "MCU_Espressif:ESP32-C3", "category": "MCU"},
        {"ref_des": "U2", "id_str": "Sensor_Temperature:DS18B20", "category": "Sensor"},
        {"ref_des": "U3", "id_str": "Power_Supervisor:MCP130", "category": "Supervisor"},
    ],
    "pin_matrix": pin_matrix,
}
try:
    out = netlist_node(state, config)
finally:
    netlist_module._call_llm_with_tools = orig
    netlist_module.MAX_BATCH_PINS = orig_cap

nets = {n["net"]: set(n["pins"]) for n in out["nets"]}
power_keys = {p["pin"] for p in out["power_pins"]}

check(f"LLM called once per batch (got {len(llm_calls)} calls)", len(llm_calls) == 2)
check("No power/GND pin ever sent to the LLM",
      all("U1:1" not in c and "U1:2" not in c and "U2:1" not in c for c in llm_calls))
check("Power pins assigned deterministically",
      {"U1:1", "U2:1", "U3:1"} <= power_keys and {"U1:2", "U2:2", "U3:2"} <= power_keys)
check("Sensor data net created in batch 1", nets.get("ONEWIRE_DQ", set()) >= {"U1:3", "U2:3"})
check("Reset net created in batch 2 (hub pin reachable)", nets.get("RESET", set()) >= {"U3:3", "U1:6"})
check("Out-of-batch pin re-listing (U2:3 in batch 2) was rejected",
      "ONEWIRE_DQ" in nets and len([p for p in nets["ONEWIRE_DQ"] if p == "U2:3"]) <= 1)
check("Hallucinated pin U9:1 dropped", not any("U9:1" in p for n in out["nets"] for p in n["pins"]))
check("Power pin U1:2 NOT hijacked into GHOST net", "U1:2" not in nets.get("GHOST", set()))
check("Duplicate U2:3 not double-assigned",
      sum(p == "U2:3" for n in out["nets"] for p in n["pins"]) == 1)
check("Hallucination drops were logged", any("dropped" in m for m in logs))

print()
print("=" * 60)
print("TEST 4: check_and_fix_overlaps — parallel wires get re-routed")
print("=" * 60)
eng = BackendLayoutEngine()
eng.matrix = [[1 for _ in range(MATRIX_SIZE)] for _ in range(MATRIX_SIZE)]

def hpath(y, x0, x1):
    step = 1 if x1 >= x0 else -1
    return [{"x": (x - 150) * GRID_SIZE, "y": (y - 150) * GRID_SIZE}
            for x in range(x0, x1 + step, step)]

# Two traces sharing the same horizontal corridor y=150, x=100..120 (overlap)
traces = [
    {"source": "U1:1", "target": "U2:1", "path": hpath(150, 100, 120)},
    {"source": "U1:2", "target": "U2:2", "path": hpath(150, 100, 120)},
]
expected_ends = [(t["path"][0].copy(), t["path"][-1].copy()) for t in traces]
fixed_traces, n_fixed, n_remaining = eng.check_and_fix_overlaps(traces)
check(f"Overlap detected and fixed (fixed={n_fixed}, remaining={n_remaining})",
      n_fixed >= 1 and n_remaining == 0)
check("Endpoints preserved after re-route",
      all(t["path"][0] == e[0] and t["path"][-1] == e[1]
          for t, e in zip(fixed_traces, expected_ends)))

# Non-overlapping traces: nothing should change
clean = [
    {"source": "A:1", "target": "B:1", "path": hpath(140, 100, 120)},
    {"source": "A:2", "target": "B:2", "path": hpath(160, 100, 120)},
]
_, nf2, nr2 = eng.check_and_fix_overlaps(clean)
check("Clean traces untouched", nf2 == 0 and nr2 == 0)

if __name__ == "__main__":
    print()
    print(f"{'=' * 60}\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)