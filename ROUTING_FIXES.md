# 🔧 CircuitBot Routing Fixes — Complete Changelog

## Summary

The routing system had **6 critical bugs** causing wires to overlap, cross component bodies, fail silently, or not appear at all. All have been fixed.

---

## 🔴 Critical Bug #1: TRACE_WEIGHT=12 Had Zero Effect (THE BIG ONE)

### Problem
The `pathfinding` library's `Grid(matrix=...)` treats matrix values as **binary** — `0` = blocked, any non-zero value = walkable. Setting cells to `TRACE_WEIGHT=12` did NOT add cost; it just marked them as walkable. So later A* routes freely ran **on top of** earlier routes.

This was the **single biggest cause of wire overlap**.

### Fix
- **Removed** the broken TRACE_WEIGHT approach entirely
- Replaced with a **custom weighted A* implementation** (`_weighted_astar()`) that:
  - Uses `CELL_TRACE = -1` to hard-block cells occupied by previous routes
  - Adds turn cost (`TURN_COST = 3`) to produce cleaner, less zig-zaggy routes
  - Uses a proper priority queue (heapq) with direction tracking
- After each successful route, `_mark_trace_cells()` marks middle cells as `CELL_TRACE` so subsequent routes **must detour around** existing wires

### Files Changed
- `agent/layout_engine.py` — new `_weighted_astar()`, `_mark_trace_cells()`, `_l_shaped_wire()` functions; removed `TRACE_WEIGHT` constant; added `CELL_FREE`, `CELL_BLOCKED`, `CELL_TRACE` constants

---

## 🔴 Critical Bug #2: WireBender WASM Silently Replaced Working Backend Routes

### Problem
The frontend ALWAYS tried to load WireBender WASM from a CDN first. If WireBender failed or produced bad routes, the backend A* routes were thrown away. If WireBender succeeded but produced mismatched coordinates, wires didn't align with pins.

### Fix
- **Made backend A* the primary router** — its output is applied immediately
- WireBender is now **opt-in only** via `localStorage.setItem('circuitbot_wirebender', 'true')`
- If WireBender fails, the backend routes remain intact (no silent replacement)

### Files Changed
- `static/app.js` — `handleAgentLayoutReady()` now applies backend layout first, only optionally tries WireBender

---

## 🔴 Critical Bug #3: Daisy-Chain Netlist Created Impossible Routing Topologies

### Problem
For a net with pins A, B, C, D, the code created daisy-chain connections A→B, B→C, C→D. This forced wires to route through intermediate component territory, creating long winding routes that crossed other nets.

### Fix
- **Replaced daisy-chain with star topology**: connect all pins to the first pin (hub) instead of chaining them sequentially
- For net with pins A, B, C, D → connections are A→B, A→C, A→D
- This avoids forcing wires through intermediate components and produces much cleaner routing

### Files Changed
- `agent/graph.py` — `netlist_node()` signal net generation section

---

## 🟡 Major Bug #4: Pin Corridors Were Only 1 Cell Wide

### Problem
Pin escape corridors were only 1 grid cell (1.27mm) wide. Multiple nets trying to reach the same component side all tried to use the same narrow corridor, causing collisions and route failures.

### Fix
- **Widened corridors to 3 cells** (3.81mm) by carving perpendicular neighbors
- Extended carve-out reach from 3 to 4 consecutive free cells
- Increased maximum carving distance from 60 to 80 iterations
- Tracks which cells were carved so overlap re-routing can restore them

### Files Changed
- `agent/layout_engine.py` — `build_obstacle_matrix()` corridor carving section

---

## 🟡 Major Bug #5: Ghost Wire Rejection Had No Fallback

### Problem
When A* produced a path longer than 4× Manhattan distance, the route was **silently dropped**. No L-shaped fallback, no direct wire — the connection just didn't appear.

### Fix
- Added `_l_shaped_wire()` — generates a simple L-shaped direct wire as a **guaranteed fallback**
- Routing now tries three approaches in order:
  1. Custom weighted A* (primary)
  2. Library A* (secondary attempt)
  3. L-shaped direct wire (guaranteed fallback)
- Relaxed ghost wire thresholds: `MAX_PATH_RATIO = 5`, `MIN_PATH_ABSOLUTE = 30`
- Routing now reports statistics: how many were A*, how many were fallback, how many failed

### Files Changed
- `agent/layout_engine.py` — `route_traces()` and new `_l_shaped_wire()`

---

## 🟡 Major Bug #6: Power Label Direction Used Wrong Bounding Box

### Problem
Power/GND symbol direction was computed from `bbox` center (which includes labels/properties), not `geom_bbox` center (physical body only). Since labels can be much larger than the component body, the direction could point the power symbol into the component body instead of away from it.

### Fix
- Changed `layout_route_node()` to use `geom_bbox` center for direction computation
- This matches the obstacle matrix and overlap resolution, which already use `geom_bbox`

### Files Changed
- `agent/graph.py` — `layout_route_node()` power label section

---

## ✨ Enhancement: Net Color Coding & Labels

### What
- Each net is now rendered in a **distinct color** from a 12-color palette
- **Net name labels** appear at the midpoint of each wire for identification
- Makes it much easier to trace connections in complex schematics

### Files Changed
- `static/renderer.js` — wire rendering section
- `agent/layout_engine.py` — traces now include `net` field

---

## ✨ Enhancement: Overlap Fix Uses Clean Matrix

### What
- `check_and_fix_overlaps()` now starts from `_original_matrix` (saved before routing) instead of the modified matrix with TRACE_WEIGHT values
- This ensures re-routed traces don't inherit phantom obstacles from previous routing attempts

### Files Changed
- `agent/layout_engine.py` — `_original_matrix` saved in `build_obstacle_matrix()`
- `agent/layout_engine.py` — `check_and_fix_overlaps()` uses `_original_matrix` as base

---

## Files Modified Summary

| File | Changes |
|------|---------|
| `agent/layout_engine.py` | Complete routing engine rewrite: custom weighted A*, 3-cell corridors, L-shaped fallback, trace hard-blocking, overlap fix from clean matrix |
| `agent/graph.py` | Star topology netlist, geom_bbox for power label direction, net field in traces |
| `static/app.js` | Backend-first routing, WireBender opt-in only |
| `static/renderer.js` | Net color coding, net name labels on wires |

## How to Enable WireBender (Optional)

If you want to try WireBender WASM routing as an enhancement:

```javascript
// In browser console:
localStorage.setItem('circuitbot_wirebender', 'true');
// To disable:
localStorage.removeItem('circuitbot_wirebender');
```
