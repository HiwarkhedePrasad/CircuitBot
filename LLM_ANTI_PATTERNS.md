# LLM Anti-Patterns: What NOT to ask the LLM to do

LLMs are autoregressive token predictors — they have no internal 2D canvas.
This document lists the patterns that cause bad output, and the deterministic
replacements you should use instead.

## Anti-patterns (BAD — produces nonsense)

### 1. Asking the LLM to emit (x, y) coordinates

```python
# ❌ BAD — LLM has no spatial awareness
prompt = f"Place component {ref} at coordinates (x, y). Output JSON: {{x, y}}"
```

**Why it fails**: The LLM predicts coordinates from training distribution.
It has no idea where U2 already is, so it emits plausible-looking but
spatially-incoherent numbers like `(154.94, 840.74)`.

### 2. Asking the LLM to draw wire paths

```python
# ❌ BAD — LLM cannot do geometric routing
prompt = f"Route a wire from pin {src} to pin {tgt}. Output JSON list of {x, y} points."
```

**Why it fails**: Wire routing requires obstacle avoidance, grid snapping,
orthogonal geometry, and clearance checks. The LLM emits points that
diagonal across the board.

### 3. Asking the LLM to "design a layout"

```python
# ❌ BAD — too vague, mixes semantic + geometric reasoning
prompt = "Design a PCB layout for an ESP32 weather station."
```

**Why it fails**: The LLM conflates "what components" with "where to put
them" and emits coordinate-laden JSON that looks plausible but is
geometrically broken.

### 4. Trusting LLM-generated DRC results

```python
# ❌ BAD — LLM cannot check geometric clearances
prompt = "Check if these traces violate DRC rules. Output {pass: bool, violations: [...]}"
```

**Why it fails**: DRC requires precise polygon intersection tests. The
LLM hallucinates pass/fail based on textual pattern matching.

## Recommended patterns (GOOD — deterministic)

### 1. LLM only does semantic selection

```python
# ✅ GOOD — LLM picks components, code picks positions
prompt = f"""Given the user's request "{user_request}", select components.
Output JSON array of {{id_str, ref_des, category, justification}}.
Do NOT include coordinates — placement is computed automatically."""
```

### 2. LLM only does net assignment

```python
# ✅ GOOD — LLM matches pins to nets, code routes the wires
prompt = f"""Given these components and their pins, group pins into nets.
Output JSON: [{{net: "I2C_SDA", pins: ["U1:3", "U2:5"]}}, ...]
Do NOT include coordinates or wire paths."""
```

### 3. LLM emits high-level design intent, code fills in geometry

```python
# ✅ GOOD — LLM describes intent, code generates the board
prompt = f"""Describe the circuit as a list of subsystems.
Output JSON: [{{subsystem: "power", function: "5V to 3V3 regulation",
                 components: ["AMS1117-3.3", "C1", "C2"]}}, ...]"""
```

### 4. Code does all geometry

```python
# ✅ GOOD — fully deterministic
from pcb_design.placement import place_components_deterministic
from pcb_design.router2 import route_nets
from pcb_design.coord_validator import sanitize_design

placements = place_components_deterministic(comps, netlist, pin_matrix)
traces = route_nets(model, netlist, pin_matrix)
design = sanitize_design(design)  # drops any bad geometry that slipped in
```

### 5. LLM as a tool-caller, not a geometry generator

```python
# ✅ GOOD — LLM picks WHICH tool to call, tool does the math
tools = [
    {"name": "calculate_trace_width",
     "description": "Compute min trace width for given current",
     "args": {"current_a": "float", "temp_rise_c": "float"}},
    {"name": "calculate_via_current",
     "description": "Compute max current for a via",
     "args": {"outer_diameter_mm": "float", "hole_diameter_mm": "float"}},
]
# LLM emits {"_tool": "calculate_trace_width", "args": {"current_a": 2.0}}
# Code runs the calculation deterministically and returns the result.
```

## Architecture recommendation

```
User prompt
     │
     ▼
┌─────────────────────────────────┐
│ LLM (semantic reasoning only)   │
│ - Analyze request               │
│ - Select components             │
│ - Assign pins to nets           │
│ - Validate component choices    │
└─────────────────────────────────┘
     │
     │  design = {comps, netlist, power_pins}  — NO coordinates
     ▼
┌─────────────────────────────────┐
│ Deterministic (geometry only)   │
│ - place_components_deterministic│
│ - route_nets (A* with DRC)      │
│ - pour_ground (thermal relief)  │
│ - sanitize_design (drop bad)    │
└─────────────────────────────────┘
     │
     │  design = {placements, traces, zones}  — clean geometry
     ▼
┌─────────────────────────────────┐
│ Export                           │
│ - generate_kicad_sch             │
│ - generate_kicad_pcb             │
└─────────────────────────────────┘
```

## If you want to add ML to placement

If you really want ML-assisted placement (not coordinate generation),
use these techniques that DON'T require the LLM to emit coordinates:

### Option A: Reinforcement learning (offline training)

Train an RL agent on a corpus of good PCB layouts:
- State: current board (component positions + netlist)
- Action: pick next component + zone (not coordinates)
- Reward: -total_wire_length - crossings × 10 - DRC_violations × 1000

The agent picks ZONES, not coordinates. The deterministic force-directed
placer handles exact (x, y) within each zone.

### Option B: Graph Neural Network for placement scoring

Train a GNN on (netlist graph, placement) → quality score pairs.
Use it to score multiple deterministic placements and pick the best.

### Option C: LLM as placement advisor (not generator)

```python
# ✅ GOOD — LLM critiques, code places
prompt = f"""Here is a component placement (ref_des, x, y, zone).
Identify any obvious problems (e.g., decoupling cap far from IC,
crystal far from MCU, USB connector in board center).
Output JSON: [{{ref: "C1", problem: "decoupling cap too far from U1"}}]
Do NOT suggest new coordinates — the placer will fix it."""
```

The LLM's textual critique is parsed and fed back as constraints to
the deterministic placer ("C1 must be within 5mm of U1").

## The bottom line

**Never let the LLM emit coordinates. Ever.** The LLM is excellent at:
- Understanding datasheets
- Picking parts that match specs
- Grouping pins into nets by function
- Validating component choices against the design intent
- Suggesting improvements in natural language

It is terrible at:
- Emitting (x, y) coordinates
- Drawing wire paths
- Checking geometric clearances
- Routing traces
- Placing components on a 2D plane

Keep the LLM in the first column, keep deterministic algorithms in
the second column, and your output will be clean.
