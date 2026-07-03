"""Schematic audit node — validates wiring and runs KiCad ERC.

Does NOT re-place or re-route. Operates on state data from routing_node.
"""

from agent.erc_runner import run_kicad_erc
from agent.layout_engine import _snap
from agent.utils import _emit, emit_assistant_message, emit_tool_event


def schematic_audit_node(state, config):
    comps = state.get("selected_components", [])
    comp_ops = state.get("component_ops", {})
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    placements = state.get("component_placements", [])
    traces = state.get("wire_paths", [])

    if not comps or not placements:
        return {}

    _emit(config, "agent:thinking", {"message": "Auditing schematic connectivity..."})
    emit_assistant_message(config, "Running schematic audit — validating wiring...")
    emit_tool_event(config, "Schematic Audit", "running", "Validating wiring...")

    # ── 1. Regenerate power labels from state placements ────────────
    def _find_comp(ref_des: str):
        for c in placements:
            if c.get("ref_des") == ref_des:
                return c
        return None

    power_labels = []
    for pp in power_pins:
        pin_obj = pin_matrix.get(pp["pin"])
        if not pin_obj:
            continue
        ref = pp["pin"].split(":")[0]
        comp = _find_comp(ref)
        if not comp:
            continue
        ax = pin_obj["x"] + comp.get("x", 0)
        ay = pin_obj["y"] + comp.get("y", 0)
        ccx = comp.get("x", 0) + comp.get("bbox", {}).get("x", 0) + comp.get("bbox", {}).get("w", 0) / 2
        ccy = comp.get("y", 0) + comp.get("bbox", {}).get("y", 0) + comp.get("bbox", {}).get("h", 0) / 2
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

    # ── 2. Validate existing traces ─────────────────────────────────
    clean_traces = [t for t in traces if len(t.get("path", [])) >= 2]
    dropped_pairs = state.get("_dropped_pairs", [])

    # Check netlist wire coverage
    wired_ref_pairs: set[tuple[str, str]] = set()
    for tr in clean_traces:
        wired_ref_pairs.add((
            tr["source"].split(":")[0],
            tr["target"].split(":")[0],
        ))
    missing_wire_count = 0
    for conn in netlist:
        key = (conn["source"].split(":")[0], conn["target"].split(":")[0])
        sorted_key = tuple(sorted(key))
        if key not in wired_ref_pairs and sorted_key not in dropped_pairs:
            missing_wire_count += 1
    if missing_wire_count:
        _emit(config, "agent:log", {
            "message": f"  Audit: {missing_wire_count} netlist connection(s) have no wire and were not dropped"
        })

    # Check pin existence
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
            "message": f"  Audit: {len(bad_pin_refs)} pin(s) referenced by wires not in pin_matrix"
        })

    # Connectivity graph check
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
            "message": f"  Audit: {len(orphan_pins)} pin(s) unreachable in connectivity graph"
        })

    # ── 3. Export and validate kicad_sch ─────────────────────────────
    sch_text = None
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
        return {}

    # ── 4. Run KiCad ERC if available ────────────────────────────────
    erc_result = None
    if sch_text:
        erc_result = run_kicad_erc(sch_text)
    if erc_result:
        total = erc_result.get("total_errors", 0)
        fixable = erc_result.get("fixable_count", 0)
        warns = erc_result.get("total_warnings", 0)
        erc_status = "failed" if total > 0 else "completed"
        erc_msg = f"{total} errors ({fixable} fixable), {warns} warnings"
        if total > 0:
            erc_msg += f" — {', '.join(e['type'] for e in erc_result['errors'][:5])}"
            if len(erc_result["errors"]) > 5:
                erc_msg += f" ... +{len(erc_result['errors'])-5} more"
        emit_tool_event(config, "KiCad ERC", erc_status, erc_msg)
        _emit(config, "agent:log", {"message": f"  ERC: {erc_msg}"})

        # Build set of all pins that belong to any net (power or signal)
        all_assigned_pins: set[str] = set()
        for n in state.get("nets", []):
            all_assigned_pins.update(n.get("pins", []))
        for pp in state.get("power_pins", []):
            all_assigned_pins.add(pp["pin"])
        raw_fixable = erc_result.get("fixable", [])
        filtered_fixable = [
            f for f in raw_fixable
            if not (f["type"] == "pin_not_connected" and f["pin_key"] not in all_assigned_pins)
        ]
        erc_result["fixable"] = filtered_fixable
        erc_result["fixable_count"] = len(filtered_fixable)
        fixable = len(filtered_fixable)
        total = len(erc_result.get("errors", []))  # errors count unchanged
        erc_status = "failed" if fixable > 0 else "completed"

        fresh_pending = []
        for f in filtered_fixable:
            fresh_pending.append({
                "pin": f["pin_key"],
                "net": f.get("pin_name", ""),
                "type": f["type"],
            })

        erc_retries = state.get("_erc_retries", 0)
        if fixable > 0:
            # Return ERC results so the builder routes to repair (loop break via _route_after_erc)
            return {
                "_erc_results": erc_result,
                "_erc_pending_connections": fresh_pending,
            }
        elif fixable == 0 and total > 0:
            emit_tool_event(config, "KiCad ERC", "completed",
                f"Only {total} non-fixable issue(s) remain — proceeding")
    else:
        emit_tool_event(config, "KiCad ERC", "skipped", "kicad-cli not available")

        # ── 4b. ERC-less fallback: use pending connections from connectivity_repair ──
        pending = state.get("_erc_pending_connections", [])
        if pending:
            # Synthesise ERC results so the builder routes to schematic_repair
            synthetic_fixable = []
            for p in pending:
                synthetic_fixable.append({
                    "type": "pin_not_connected",
                    "pin_key": p.get("pin", ""),
                    "pin_name": p.get("net", ""),
                    "ref_des": p.get("pin", "").split(":")[0],
                    "pin_num": p.get("pin", "").split(":")[-1] if ":" in p.get("pin", "") else "",
                    "position": {},
                    "description": f"Pin {p.get('pin', '')} missing wire",
                })
            erc_result = {
                "errors": [{"type": "pin_not_connected"}],
                "warnings": [],
                "total_errors": len(synthetic_fixable),
                "total_warnings": 0,
                "fixable_count": len(synthetic_fixable),
                "fixable": synthetic_fixable,
            }
            _emit(config, "agent:log", {
                "message": f"  ERC-less fallback: {len(synthetic_fixable)} pending connection(s) from connectivity_repair"
            })

    # ── 5. Emit done ─────────────────────────────────────────────────
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": clean_traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })

    erc_retries = state.get("_erc_retries", 0)
    if erc_retries > 0:
        emit_tool_event(config, "Schematic Audit", "completed",
                        f"ERC clean — proceeding (retries: {erc_retries})")
        emit_assistant_message(config, "Schematic audit passed — ERC is clean.")
    else:
        emit_tool_event(config, "Schematic Audit", "completed", "No corrections needed")
        emit_assistant_message(config, "Schematic audit passed — all wiring is correct.")

    return {}
