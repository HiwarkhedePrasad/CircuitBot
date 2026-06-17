# CircuitBot Fixes Applied

## Issues Fixed

### 1. **Duplicate Component Selection (e.g., Two ESP32s)**

**Problem:** The agent was selecting the same component multiple times for different subsystems.

**Root Cause:** The `select_node()` function didn't deduplicate components by `id_str`.

**Fix Applied:**
- Added deduplication logic in `agent/graph.py` → `select_node()`
- Tracks `seen_ids` set to prevent duplicate `id_str` values
- Tracks `seen_refs` set to ensure unique reference designators
- Auto-renames duplicate reference designators (e.g., U1 → U2)
- Logs skipped duplicates for debugging

**Code Changes:**
```python
# CRITICAL FIX: Deduplicate components by id_str
seen_ids = set()
seen_refs = set()
deduped = []

for s in selected:
    if s["id_str"] in seen_ids:
        _emit(config, "agent:log", {"message": f"  Skipped duplicate: {s['id_str']}"})
        continue
    # ... deduplication logic
```

---

### 2. **Incorrect Pin Connections**

**Problem:** Pins were connecting to non-existent pins or creating invalid connections.

**Root Cause:** 
- No validation that pins exist before creating netlist connections
- LLM could hallucinate pin keys
- Fallback netlist generator connected ALL pins with same name (even across unrelated components)

**Fixes Applied:**

#### A. Netlist Validation (`netlist_node()`)
- Validates every connection before adding to netlist
- Checks both source and target pins exist in `pin_matrix`
- Prevents self-connections (pin to itself)
- Logs invalid connections for debugging

```python
# CRITICAL FIX: Validate all netlist connections
for conn in netlist:
    src = conn.get("source", "")
    tgt = conn.get("target", "")
    
    if src not in pins or tgt not in pins:
        _emit(config, "agent:log", {"message": f"  Invalid connection: {src} → {tgt}"})
        continue
```

#### B. Improved Fallback Netlist Generator (`_generate_netlist_fallback()`)
- **Priority 1:** Connect all GND pins together (star topology)
- **Priority 2:** Connect power rails by voltage (3V3, 5V, VBAT separately)
- **Priority 3:** Only connect signal pins if **exactly 2 pins** match (prevents connecting all GPIOs)
- Normalizes pin names (uppercase) for better matching
- Handles common pin name variations (GND/GROUND/VSS, VCC/VDD/3V3)

```python
# Only connect if exactly 2 pins (avoid connecting all GPIOs together)
if len(keys) == 2:
    netlist.append({"source": keys[0], "target": keys[1]})
```

#### C. Enhanced Pin Extraction (`_extract_pins_from_ops()`)
- Handles all rotation angles (not just 0/90/180/270)
- Validates pin numbers before adding
- Prevents duplicate pin numbers (can occur with inherited symbols)
- Strips whitespace from pin names/numbers
- Better error handling for malformed S-expressions

```python
# Avoid duplicate pin numbers
if key in pin_matrix:
    continue
```

---

### 3. **Improved LLM Prompts**

**Problem:** LLM wasn't explicitly told to avoid duplicates or validate pin keys.

**Fixes Applied:**

#### A. Selection Prompt (`SELECT_SYSTEM`)
Added explicit rules:
- "NEVER select the same component (same id_str) more than once"
- "Each id_str must appear only ONCE in your output"
- "If multiple subsystems need similar components, select DIFFERENT id_str values"

#### B. Netlist Prompt (`NETLIST_SYSTEM`)
Added explicit rules:
- "You MUST ONLY use pin keys that appear EXACTLY in the 'Available pins' list"
- "NEVER invent or modify pin keys"
- "NEVER connect a pin to itself"
- Clearer formatting for pin key format: "REF:pin_number"

---

### 4. **Better Debugging Logs**

**Added:**
- Pin details logging: Shows first 5 pins with names when component is loaded
- Duplicate component warnings
- Invalid connection warnings
- Reference designator renaming notifications

**Example Log Output:**
```
✓ Added U1 (38 pins): 1:GND, 2:3V3, 3:EN, 4:GPIO36, 5:GPIO39... +33 more
⚠ Skipped duplicate: MCU_Module:ESP32-WROOM-32
✓ Renamed U1 → U2 (duplicate ref)
⚠ Invalid connection: U3:99 does not exist
✓ Generated 12 valid connections
```

---

## Testing Recommendations

1. **Test with duplicate-prone prompts:**
   ```
   "ESP32 with temperature sensor and display"
   "Two microcontrollers communicating via I2C"
   ```

2. **Verify pin connections:**
   - Check that GND pins are all connected
   - Check that power rails match (3V3 to 3V3, not 3V3 to 5V)
   - Check that signal pins make logical sense

3. **Check component list:**
   - No duplicate components should appear
   - All reference designators should be unique

---

## Files Modified

1. `agent/graph.py`
   - `select_node()` - Added deduplication
   - `netlist_node()` - Added validation
   - `_generate_netlist_fallback()` - Smarter connection logic
   - `_extract_pins_from_ops()` - Better pin extraction
   - `dispatch_node()` - Enhanced logging

2. `agent/prompts.py`
   - `SELECT_SYSTEM` - Added anti-duplicate rules
   - `NETLIST_SYSTEM` - Added pin validation rules

---

## Next Steps (Optional Improvements)

1. **Add component type validation:**
   - Prevent selecting 3 resistors when only 1 is needed
   - Validate component counts match subsystem requirements

2. **Add electrical rule checking (ERC):**
   - Warn if connecting 5V to 3.3V pin
   - Warn if power pin has no connection
   - Warn if output pins are shorted together

3. **Add pin type awareness:**
   - Use KiCad pin types (power_in, power_out, input, output, bidirectional)
   - Prevent connecting two outputs together
   - Ensure power_in pins connect to power_out pins

4. **Add visual feedback:**
   - Highlight duplicate components in red
   - Show connection validation errors in UI
   - Display pin compatibility warnings

---

## How to Test

1. Restart the server:
   ```bash
   python server.py
   ```

2. Try a prompt that previously caused duplicates:
   ```
   "ESP32 with battery charger and status LED"
   ```

3. Check the agent log for:
   - "Skipped duplicate" messages
   - "Invalid connection" warnings
   - Pin details for each component

4. Verify the schematic:
   - Only one ESP32 should appear
   - All GND pins should be connected
   - Power connections should be logical