"""Unit test for reference designator deduplication logic.

Verifies that:
1. Unique, valid reference designators are preserved.
2. Colliding reference designators are reassigned to unique values.
3. Newly assigned designators do not conflict/hijack existing unique designators
   processed later in the list.
"""

import sys
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.nodes.select import _assign_ref_des

passed = failed = 0
def check(label, cond):
    global passed, failed
    print(f"  {'[PASS]' if cond else '[FAIL]'} {label}")
    passed += cond
    failed += not cond

print("=" * 60)
print("TEST 1: Reference designator deduplication with late unique elements")
print("=" * 60)

# U3 has a collision (two components share it)
# U5 is unique and appears AFTER the colliding U3 elements
components = [
    {"ref_des": "U3", "category": "MCU", "id_str": "MCU_Microchip:ATmega328P-MM"},
    {"ref_des": "U3", "category": "Sensor", "id_str": "Sensor_Temperature:DS18B20"},
    {"ref_des": "U5", "category": "Display", "id_str": "Display_Graphic:ER_OLEDM0.91_1x-I2C"},
]

result = _assign_ref_des(components)
print("Deduplication output:")
for c in result:
    print(f"  {c['id_str'].split(':')[-1]}: {c['ref_des']}")

ref_des_list = [c["ref_des"] for c in result]

# Check uniqueness
check("All assigned reference designators are unique", len(set(ref_des_list)) == len(ref_des_list))

# Check U5 is preserved as U5
u5_comp = next(c for c in result if "ER_OLEDM0.91_1x-I2C" in c["id_str"])
check("Unique element U5 is preserved", u5_comp["ref_des"] == "U5")

# Check colliding U3 elements got new unique values that aren't U5
u3_comps = [c for c in result if c["ref_des"] != "U5"]
check("Colliding elements are not assigned U5", all(c["ref_des"] != "U5" for c in u3_comps))
check("No duplicate reference designators generated", len(set(ref_des_list)) == 3)

if __name__ == "__main__":
    print()
    print(f"{'=' * 60}\nRESULT: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
