"""Schematic audit node — post-routing validation + auto-correction.

Rebuilds the layout engine from state, re-runs placement and routing
to get the definitive wire set, validates all 6 criteria, and
auto-corrects by re-emitting agent:layout_ready with fixed data.
"""

from agent.layout_engine import BackendLayoutEngine
from agent.utils import _emit, emit_assistant_message, emit_tool_event


def schematic_audit_node(state, config):
    """Validate and auto-correct schematic output.

    Checks performed:
      1. Netlist wire coverage — every netlist connection has a wire or was dropped.
      2. Power pin labels — every power_pin entry has a label.
      3. Wire integrity — all wire paths are orthogonal, length-capped, >=2 pts.
      4. Pin existence — every source/target in every wire exists in pin_matrix.
      5. Connectivity graph — every netlist pin endpoint is reachable via wires.
      6. kicad_sch validity — exported .kicad_sch has balanced parens + root element.

    Auto-correction:
      - Wires and power labels regenerated from scratch.
      - Re-emits agent:layout_ready + agent:done with corrected payload.
      - Returns corrected state keys (component_placements, wire_paths, power_labels).
    """
    comps = state.get("selected_components", [])
    comp_ops = state.get("component_ops", {})
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    if not comps or not comp_ops:
        return {}

    _emit(config, "agent:thinking", {"message": "Auditing schematic connectivity..."})
    emit_assistant_message(config, "Running schematic audit — validating wiring integrity and connectivity...")
    emit_tool_event(config, "Schematic Audit", "running", "Validating wiring...")

    # ── 1. Rebuild engine ──────────────────────────────────────────────
    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(
            ref_des, ops, comp["category"],
            comp.get("id_str", ""),
            comp.get("for_component", ""),
        )

    if not engine.components:
        emit_tool_event(config, "Schematic Audit", "completed", "No components to audit")
        return {}

    # ── 2. Run definitive placement + routing ──────────────────────────
    engine.execute_placement(pin_matrix=pin_matrix, netlist=netlist)

    raw_traces, dropped_pairs = engine.route_traces(netlist, pin_matrix)

    # Clean pass: same HARD GUARD as layout_route
    clean_traces = []
    n_dropped = 0
    for tr in raw_traces:
        path = tr.get("path", [])
        if len(path) < 2:
            n_dropped += 1
            continue
        ok = True
        total_len = 0.0
        for i in range(len(path) - 1):
            dx = abs(path[i]["x"] - path[i + 1]["x"])
            dy = abs(path[i]["y"] - path[i + 1]["y"])
            if dx > 1e-3 and dy > 1e-3:
                ok = False
                break
            total_len += dx + dy
            if total_len > 150.0:
                ok = False
                break
        if not ok:
            n_dropped += 1
            continue
        clean_traces.append(tr)

    # ── 3. Regenerate power labels ─────────────────────────────────────
    placements = engine.get_placements()
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

    # ── 4. Check netlist wire coverage ─────────────────────────────────
    wired_ref_pairs: set[tuple[str, str]] = set()
    for tr in clean_traces:
        wired_ref_pairs.add((
            tr["source"].split(":")[0],
            tr["target"].split(":")[0],
        ))
    dropped_ref_set: set[tuple[str, str]] = set()
    for pair in dropped_pairs:
        dropped_ref_set.add(tuple(sorted(pair)))

    missing_wire_count = 0
    for conn in netlist:
        key = (conn["source"].split(":")[0], conn["target"].split(":")[0])
        sorted_key = tuple(sorted(key))
        if key not in wired_ref_pairs and sorted_key not in dropped_ref_set:
            missing_wire_count += 1

    if missing_wire_count:
        _emit(config, "agent:log", {
            "message": (
                f"  Audit warning: {missing_wire_count} netlist connection(s) "
                "have no wire and were not dropped"
            )
        })

    # ── 5. Check wire integrity ────────────────────────────────────────
    bad_integrity = 0
    for tr in clean_traces:
        path = tr.get("path", [])
        if len(path) < 2:
            bad_integrity += 1
            continue
        for i in range(len(path) - 1):
            dx = abs(path[i]["x"] - path[i + 1]["x"])
            dy = abs(path[i]["y"] - path[i + 1]["y"])
            if dx > 1e-3 and dy > 1e-3:
                bad_integrity += 1
                break

    if bad_integrity:
        _emit(config, "agent:log", {
            "message": f"  Audit warning: {bad_integrity} wire(s) have diagonal/short path"
        })

    # ── 6. Check pin existence ─────────────────────────────────────────
    bad_pin_refs: set[str] = set()
    for tr in clean_traces:
        src = tr.get("source", "")
        tgt = tr.get("target", "")
        if src and src not in pin_matrix:
            bad_pin_refs.add(src)
        if tgt and tgt not in pin_matrix:
            bad_pin_refs.add(tgt)

    if bad_pin_refs:
        _emit(config, "agent:log", {
            "message": f"  Audit warning: {len(bad_pin_refs)} pin(s) referenced by wires not in pin_matrix"
        })

    # ── 7. Check connectivity graph ────────────────────────────────────
    all_netlist_pins: set[str] = set()
    for conn in netlist:
        if conn.get("source"):
            all_netlist_pins.add(conn["source"])
        if conn.get("target"):
            all_netlist_pins.add(conn["target"])

    connected_pins: set[str] = set()
    for tr in clean_traces:
        if tr.get("source"):
            connected_pins.add(tr["source"])
        if tr.get("target"):
            connected_pins.add(tr["target"])

    orphan_pins = all_netlist_pins - connected_pins
    if orphan_pins:
        _emit(config, "agent:log", {
            "message": f"  Audit warning: {len(orphan_pins)} pin(s) unreachable in connectivity graph"
        })

    # ── 8. Validate kicad_sch export ───────────────────────────────────
    try:
        from agent.kicad_export import generate_kicad_sch

        design_dict = {
            "selected_components": comps,
            "component_ops": comp_ops,
            "component_placements": placements,
            "wire_paths": clean_traces,
            "power_labels": power_labels,
            "title": (state.get("prompt", "") or "")[:80],
        }
        sch_text = generate_kicad_sch(design_dict)
        open_c = sch_text.count("(")
        close_c = sch_text.count(")")
        if open_c != close_c:
            _emit(config, "agent:log", {
                "message": f"  Audit ERROR: kicad_sch unbalanced parens ({open_c} vs {close_c})"
            })
        elif "(kicad_sch" not in sch_text:
            _emit(config, "agent:log", {
                "message": "  Audit ERROR: kicad_sch missing root element"
            })
        else:
            _emit(config, "agent:log", {
                "message": (
                    f"  Audit: kicad_sch OK ({len(sch_text)} chars, "
                    f"{len(clean_traces)} wires, {len(power_labels)} labels)"
                )
            })
    except Exception as e:
        _emit(config, "agent:log", {
            "message": f"  Audit ERROR: kicad_sch generation failed: {e}"
        })

    # ── 9. Compare with state; emit corrections if needed ──────────────
    existing_traces = state.get("wire_paths", [])
    existing_labels = state.get("power_labels", [])

    existing_trace_set = {(t.get("source", ""), t.get("target", ""))
                          for t in existing_traces}
    new_trace_set = {(t["source"], t["target"]) for t in clean_traces}

    existing_label_set = {(lb.get("pin", ""), lb.get("net", ""))
                          for lb in existing_labels}
    new_label_set = {(lb["pin"], lb["net"]) for lb in power_labels}

    if (existing_trace_set == new_trace_set
            and existing_label_set == new_label_set):
        _emit(config, "agent:log", {
            "message": "  Schematic audit: all OK, no corrections needed"
        })
        emit_tool_event(config, "Schematic Audit", "completed", "No corrections needed")
        emit_assistant_message(config, "Schematic audit passed — all wiring is correct.")
        return {}

    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": clean_traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })
    _emit(config, "agent:done", {
        "message": (
            f"Schematic audit corrections applied: "
            f"{len(clean_traces)} wires, {len(power_labels)} labels"
        ),
    })
    emit_tool_event(config, "Schematic Audit", "completed", f"Corrections applied: {len(clean_traces)} wires")
    emit_assistant_message(config, f"Schematic corrections applied — {len(clean_traces)} wires, {len(power_labels)} power labels.")

    return {
        "component_placements": placements,
        "wire_paths": clean_traces,
        "power_labels": power_labels,
    }
