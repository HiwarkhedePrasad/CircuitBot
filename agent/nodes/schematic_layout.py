"""Schematic layout node — component placement + orthogonal wire routing.

Extracted from the former monolithic layout_route_node. Handles:
  1. Build BackendLayoutEngine from selected components + symbol ops.
  2. Schematic tier-based placement (grid-packed).
  3. Obstacle-aware orthogonal wire routing with retry loop
     (tightens satellite placement when wires fail).
  4. Power label position/direction computation.
  5. Emit agent:layout_ready event for the frontend.
"""

import random

from agent.layout_engine import BackendLayoutEngine, _snap
from agent.utils import _emit, emit_assistant_message, emit_tool_event


def schematic_layout_node(state, config):
    _emit(config, "agent:thinking", {"message": "Computing schematic layout and routing wires..."})
    emit_assistant_message(config, "Placing components on the schematic sheet and routing wires between them...")
    emit_tool_event(config, "Schematic Layout", "running", "Placing components and routing wires...")

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])

    # If this is an ERC repair re-run, increment the retry counter
    erc_fix_pins = state.get("_erc_fix_pins", [])
    erc_retries = state.get("_erc_retries", 0)
    if erc_fix_pins:
        erc_retries += 1

    if not comps or not comp_ops:
        emit_tool_event(config, "Schematic Layout", "failed", "No components to place.")
        return {}

    # ── 1. Build engine ────────────────────────────────────────────
    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(ref_des, ops, comp["category"],
                             comp.get("id_str", ""),
                             comp.get("for_component", ""))

    if not engine.components:
        emit_tool_event(config, "Schematic Layout", "failed", "No components could be placed.")
        return {}

    # ── 2/3. Schematic placement + routing with score-based retry loop ──
    MAX_WIRE_LEN = 150.0
    MAX_ROUTE_RETRIES = 5

    best_score = float('inf')
    best_traces: list | None = None
    best_placements: list | None = None
    all_dropped_pairs: list[tuple[str, str]] = []

    def _trace_to_points(tr: dict) -> list[tuple[float, float]]:
        path = tr.get('path') or tr.get('points', [])
        if path and isinstance(path[0], dict):
            return [(p['x'], p['y']) for p in path]
        return path  # already list of tuples

    def _compute_score(pts_list: list[list[tuple[float, float]]],
                       n_dropped: int, n_dropped_pairs: int) -> tuple[float, int]:
        total_len = 0.0
        crossings = 0
        segments: list = []
        for pts in pts_list:
            for i in range(len(pts) - 1):
                dx = abs(pts[i + 1][0] - pts[i][0])
                dy = abs(pts[i + 1][1] - pts[i][1])
                total_len += dx + dy
                segments.append((pts[i][0], pts[i][1],
                                 pts[i + 1][0], pts[i + 1][1]))

        def _orient(ax, ay, bx, by, cx, cy):
            v = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
            if abs(v) < 1e-12:
                return 0
            return 1 if v > 0 else -1

        def _on_seg(ax, ay, bx, by, cx, cy):
            return (min(ax, bx) <= cx <= max(ax, bx) and
                    min(ay, by) <= cy <= max(ay, by))

        for i in range(len(segments)):
            for j in range(i + 1, len(segments)):
                ax1, ay1, ax2, ay2 = segments[i]
                bx1, by1, bx2, by2 = segments[j]
                if (abs(ax2 - bx1) < 1e-9 and abs(ay2 - by1) < 1e-9) or \
                   (abs(ax1 - bx2) < 1e-9 and abs(ay1 - by2) < 1e-9):
                    continue
                if (abs(ax1 - bx1) < 1e-9 and abs(ay1 - by1) < 1e-9) or \
                   (abs(ax2 - bx2) < 1e-9 and abs(ay2 - by2) < 1e-9):
                    continue
                o1 = _orient(ax1, ay1, ax2, ay2, bx1, by1)
                o2 = _orient(ax1, ay1, ax2, ay2, bx2, by2)
                o3 = _orient(bx1, by1, bx2, by2, ax1, ay1)
                o4 = _orient(bx1, by1, bx2, by2, ax2, ay2)
                if o1 != o2 and o3 != o4:
                    crossings += 1

        return (float(n_dropped_pairs * 10000 + n_dropped * 10000 +
                      crossings * 1000 + total_len),
                crossings)

    def _save_placement(e) -> list:
        return [{'x': c['x'], 'y': c['y']} for c in e.components]

    def _restore_placement(e, snap: list) -> None:
        for c, s in zip(e.components, snap):
            c['x'] = s['x']
            c['y'] = s['y']

    for retry in range(MAX_ROUTE_RETRIES):
        engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

        # Block-detection logging (blocks_v2 mode)
        if retry == 0 and engine._last_block_map:
            from collections import Counter
            block_counts: dict[str, int] = Counter(engine._last_block_map.values())
            blocks_str = "; ".join(f"{b}({n})" for b, n in
                                   sorted(block_counts.items(),
                                          key=lambda kv: -kv[1]))
            emit_tool_event(config, "Block Detection", "completed",
                            f"Detected {len(block_counts)} blocks: {blocks_str}")

        # Save placement after first layout (for score-based revert)
        if retry == 0:
            prev_placement = _save_placement(engine)

        raw_traces, dropped_pairs = engine.route_traces(netlist, pin_matrix)
        all_dropped_pairs = dropped_pairs

        # Filter traces
        clean_traces = []
        n_dropped = 0
        for tr in raw_traces:
            path = tr.get('path', [])
            if len(path) < 2:
                n_dropped += 1
                continue
            ok = True
            total_len = 0.0
            for i in range(len(path) - 1):
                dx = abs(path[i]['x'] - path[i + 1]['x'])
                dy = abs(path[i]['y'] - path[i + 1]['y'])
                if dx > 1e-3 and dy > 1e-3:
                    ok = False
                    break
                total_len += dx + dy
                if total_len > MAX_WIRE_LEN:
                    ok = False
                    break
            if not ok:
                n_dropped += 1
                continue
            clean_traces.append(tr)

        total_pre = len(raw_traces) + n_dropped + len(dropped_pairs)

        # Compute score
        pts_list = [_trace_to_points(tr) for tr in clean_traces]
        score, crossings = _compute_score(pts_list, n_dropped, len(dropped_pairs))

        total_wire = (sum(sum(abs(pts[i+1][0]-pts[i][0])+abs(pts[i+1][1]-pts[i][1])
                             for i in range(len(pts)-1))
                         for pts in pts_list)
                      if pts_list else 0.0)

        _emit(config, "agent:log", {
            "message": (f"  Retry {retry+1}/{MAX_ROUTE_RETRIES}: "
                        f"score={score:.0f} (drops={n_dropped}+{len(dropped_pairs)} "
                        f"cross={crossings} "
                        f"wire={total_wire:.1f})")
        })

        # Accept or reject based on score
        if score <= best_score:
            best_score = score
            best_traces = clean_traces
            best_placements = engine.get_placements()
            prev_placement = _save_placement(engine)

            dropped_total = n_dropped + len(dropped_pairs)
            summary = f"score={score:.0f}, {len(clean_traces)} wires routed"
            if dropped_total:
                summary += f", {dropped_total} dropped"
            emit_tool_event(config, f"Routing retry {retry+1}/{MAX_ROUTE_RETRIES}",
                            "completed" if dropped_total == 0 else "running",
                            summary,
                            details=f"Crossings: {crossings}\nWire length: {total_wire:.1f}mm\nDropped: {dropped_total}")

            no_dropped = dropped_total == 0
            if no_dropped or retry == MAX_ROUTE_RETRIES - 1:
                sch_traces = best_traces
                break
        else:
            # Score got worse — revert to best placement and repair
            _restore_placement(engine, prev_placement)
            n_moved = engine._repair_placement_for_routing(all_dropped_pairs)
            engine._enforce_satellite_distance(
                engine._build_parent_map(netlist))

            # Inject random jitter (±2.5mm) to break deterministic layout
            mains = [c for c in engine.components if c['tier'] >= 0]
            for c in mains:
                c['x'] += random.uniform(-2.5, 2.5)
                c['y'] += random.uniform(-2.5, 2.5)

            emit_tool_event(config, f"Routing retry {retry+1}/{MAX_ROUTE_RETRIES}",
                            "failed",
                            f"Score {score:.0f} ≥ best {best_score:.0f}, reverted + tightened {n_moved}",
                            details=f"Previous best: {best_score:.0f}\nAttempt: {score:.0f}")

    if best_traces is not None:
        sch_traces = best_traces

    placements = best_placements if best_placements is not None else engine.get_placements()

    # ── 4. Power labels ────────────────────────────────────────────
    power_labels = []
    for pp in power_pins:
        pin_obj = pin_matrix.get(pp["pin"])
        if not pin_obj:
            continue
        ref = pp["pin"].split(":")[0]
        comp = engine._get_comp(ref)
        if not comp:
            continue
        ax = pin_obj["x"] + comp["x"]
        ay = pin_obj["y"] + comp["y"]
        ccx = comp["x"] + comp["bbox"]["x"] + comp["bbox"]["w"] / 2
        ccy = comp["y"] + comp["bbox"]["y"] + comp["bbox"]["h"] / 2
        dx = ax - ccx
        dy = ay - ccy
        if abs(dx) < abs(dy):
            direction = "up" if dy >= 0 else "down"
        else:
            direction = "right" if dx >= 0 else "left"
        power_labels.append({
            "pin": pp["pin"],
            "net": pp["net"],
            "x": _snap(ax),
            "y": _snap(ay),
            "dir": direction,
        })

    # ── 5. Emit layout_ready ───────────────────────────────────────
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": sch_traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })

    emit_tool_event(config, "Schematic Layout", "completed", f"Laid out {len(placements)} components, routed {len(sch_traces)} wires")
    emit_assistant_message(config, f"Layout complete — {len(placements)} components placed, {len(sch_traces)} wires routed on the schematic.")

    result = {
        "component_placements": placements,
        "wire_paths": sch_traces,
        "power_labels": power_labels,
    }
    if erc_fix_pins:
        result["_erc_retries"] = erc_retries
        result["_erc_fix_pins"] = []  # clear so next audit doesn't re-trigger
        emit_tool_event(config, "ERC Repair Re-route", "completed",
            f"Re-routed with {len(erc_fix_pins)} added connections (retry {erc_retries}/3)")
    return result
