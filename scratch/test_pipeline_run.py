"""End-to-end verification of circuit generation pipeline nodes."""

from agent.nodes.analyze import _fallback_analysis
from agent.nodes.research import research_node
from agent.nodes.repair import repair_node
from agent.templates.matcher import get_library_filter
from agent.utils import _ref_prefix_for

# 1. Test analysis & library filter matching
subsystems = _fallback_analysis("Design a Temperature Sensor Board with ESP32-C3, DS18B20, and BME280")
print("Analyzed Subsystems:")
for s in subsystems:
    lib_filt = get_library_filter(s)
    print(f"  - {s['subsystem']}: filter='{lib_filt}'")
    assert lib_filt != "", f"Subsystem {s['subsystem']} should have a non-empty library filter"

# 2. Test repair node reference prefix correction
state = {
    "selected_components": [
        {"ref_des": "U101", "id_str": "RF_Module:ESP32-WROOM-32U", "category": "RF_Module"},
        {"ref_des": "U201", "id_str": "Sensor:BME280", "category": "Sensor"},
        {"ref_des": "U301", "id_str": "Sensor_Temperature:DS18B20Z", "category": "Sensor_Temperature"},
        {"ref_des": "U1", "id_str": "Regulator_Linear:NCP163AFCT330T2G", "category": "Regulator_Linear"},
        {"ref_des": "R2", "id_str": "Device:C_US", "category": "Device"},  # Mismatched!
    ],
    "repairable_errors": [],
    "repair_passes_used": 0,
}

res = repair_node(state, config={"configurable": {}})
comps = res["selected_components"]
cap = next(c for c in comps if c["id_str"] == "Device:C_US")

print("\nRepair Prefix Correction Result:")
print(f"  Device:C_US reference designator = {cap['ref_des']}")
assert cap['ref_des'].startswith('C'), f"Expected C*, got {cap['ref_des']}"

print("\nPipeline Verification Successful!")
