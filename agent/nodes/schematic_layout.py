"""Schematic layout node — component placement + orthogonal wire routing.

Extracted from the former monolithic layout_route_node. Handles:
  1. Build BackendLayoutEngine from selected components + symbol ops.
  2. Schematic tier-based placement (grid-packed).
  3. Obstacle-aware orthogonal wire routing with retry loop
     (tightens satellite placement when wires fail).
  4. Power label position/direction computation.
  5. Emit agent:layout_ready event for the frontend.
"""

from agent.layout_engine import BackendLayoutEngine
from agent.utils import _emit, _emit_activity


def schematic_layout_node(state, config):
    _emit(config, "agent:thinking", {"message": "Computing schematic layout and routing wires..."})
    _emit_activity(config, "schematic_layout", "Schematic Layout", "start")

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])

    if not comps or not comp_ops:
        _emit(config, "agent:log", {"message": "No components to place."})
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
        _emit(config, "agent:log", {"message": "No components could be placed."})
        return {}

    # ── 2/3. Schematic placement + routing with retry loop ─────────
    MAX_WIRE_LEN = 150.0
    MAX_ROUTE_RETRIES = 3
    all_dropped_pairs: list[tuple[str, str]] = []

    for retry in range(MAX_ROUTE_RETRIES):
        engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)
        raw_traces, dropped_pairs = engine.route_traces(netlist, pin_matrix)
        all_dropped_pairs = dropped_pairs

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
        n_retry_dropped = len(all_dropped_pairs) + n_dropped
        no_dropped = n_retry_dropped == 0 or (
            retry > 0 and n_retry_dropped < (total_pre * 0.1)
        )

        if no_dropped or retry == MAX_ROUTE_RETRIES - 1:
            sch_traces = clean_traces
            _emit(config, "agent:log", {
                "message": (f"  Schematic: routed {len(clean_traces)}/{total_pre} signal wires "
                            f"(retry {retry+1}, dropped {n_dropped}+{len(all_dropped_pairs)})")
            })
            break

        n_moved = engine._repair_placement_for_routing(all_dropped_pairs)
        _emit(config, "agent:log", {
            "message": (f"  Schematic retry {retry+1}/{MAX_ROUTE_RETRIES}: "
                        f"{len(all_dropped_pairs)} wire(s) dropped, "
                        f"tightened {n_moved} component(s)")
        })

    placements = engine.get_placements()

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
            "x": ax,
            "y": ay,
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

    _emit_activity(config, "schematic_layout", "Schematic Layout", "done")

    return {
        "component_placements": placements,
        "wire_paths": sch_traces,
        "power_labels": power_labels,
    }
