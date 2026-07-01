"""Routing node — runs wire routing using existing placements.

Handles ERC repair attach requests by merging pending connections
into the netlist before routing. Never re-runs placement.
"""

from agent.layout_engine import BackendLayoutEngine, _snap
from agent.validation import ValidationIssue
from agent.utils import _emit, emit_assistant_message, emit_tool_event


def _find_attachment_buddy(pin: str, net_name: str,
                           netlist: list, nets: list,
                           pin_matrix: dict,
                           engine_components: list,
                           power_pins: list | None = None) -> str | None:
    """Pick the best existing endpoint on the same net to wire to.

    Strategy: topology first (prefer leaf nodes with degree 1),
    geometry second (shortest Manhattan distance as tiebreaker).
    Falls back to power_pins on the same net when no wired candidate exists.
    """
    # All pins on this net from the netlist's nets dict
    net_pins = []
    for n in nets:
        if n["net"] == net_name:
            net_pins = n["pins"]
            break

    if not net_pins:
        return None

    # Count how many connections each pin already has in netlist
    degree: dict[str, int] = {}
    for c in netlist:
        s, t = c["source"], c["target"]
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1

    # Candidates = pins on this net that are NOT the target pin
    # and DO have at least one connection already (degree >= 1)
    candidates = [p for p in net_pins if p != pin and degree.get(p, 0) >= 1]

    # Fallback: if no wired candidates, include power_pin entries on the same net
    # (they have degree 0 but are valid connection endpoints)
    if not candidates and power_pins:
        for pp in power_pins:
            if pp["net"].upper() == net_name.upper() and pp["pin"] != pin:
                if pp["pin"] in net_pins:
                    candidates.append(pp["pin"])

    if not candidates:
        return None

    # Prefer leaf (degree == 1), then lowest degree, then Manhattan distance
    pin_pos = pin_matrix.get(pin)
    if pin_pos:
        def _dist(p: str) -> float:
            pos = pin_matrix.get(p)
            if not pos or not pin_pos:
                return float("inf")
            return abs(pos["x"] - pin_pos["x"]) + abs(pos["y"] - pin_pos["y"])
    else:
        def _dist(p: str) -> float:
            return float("inf")

    # Sort: degree asc, Manhattan asc
    candidates.sort(key=lambda p: (degree.get(p, 0), _dist(p)))
    return candidates[0]


def routing_node(state, config):
    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    nets = state.get("nets", [])
    placements = state.get("component_placements", [])

    erc_pending = state.get("_erc_pending_connections", [])
    erc_retries = state.get("_erc_retries", 0)

    if not comps or not comp_ops:
        emit_tool_event(config, "Routing", "failed", "No components to route.")
        return {}

    _emit(config, "agent:thinking", {"message": "Routing wires on the schematic..."})
    msg = "Re-routing with ERC repair connections..." if erc_pending else "Routing wires..."
    emit_assistant_message(config, msg)
    emit_tool_event(config, "Routing", "running",
                    "Re-routing..." if erc_pending else "Routing wires...")

    # ── 1. Build engine and restore placements ──────────────────────
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
        emit_tool_event(config, "Routing", "failed", "No components could be routed.")
        return {}

    # Restore saved placements (no re-placement)
    for c in engine.components:
        match = next((p for p in placements if p["ref_des"] == c["ref_des"]), None)
        if match:
            c["x"] = match["x"]
            c["y"] = match["y"]
            c["rotation"] = match.get("rotation", 0)

    # ── 2. Merge ERC pending connections into netlist ───────────────
    working_netlist = list(netlist)
    if erc_pending:
        for req in erc_pending:
            pin_key = req["pin"]
            net_name = req["net"]
            # If the pin already has netlist connections but NO physical wire,
            # remove the stale connections so the router retries with a fresh
            # topology choice from _find_attachment_buddy.
            stale = [c for c in working_netlist
                     if pin_key in (c["source"], c["target"])]
            for s in stale:
                working_netlist.remove(s)
            buddy = _find_attachment_buddy(pin_key, net_name,
                                           working_netlist, nets,
                                           pin_matrix, engine.components,
                                           power_pins)
            if buddy:
                working_netlist.append({
                    "source": buddy,
                    "target": pin_key,
                    "net": net_name,
                })

    # ── 3. Pin-coverage validation (PCV) ───────────────────────────
    all_pins = set(pin_matrix.keys())
    net_pins = set()
    for n in nets:
        for p in n.get("pins", []):
            net_pins.add(p)
    wired_pins = set()
    for t in state.get("wire_paths", []):
        src = t.get("source", "")
        tgt = t.get("target", "")
        if src: wired_pins.add(src)
        if tgt: wired_pins.add(tgt)
    power_pin_keys = set(pp["pin"] for pp in power_pins)
    assigned_pins = net_pins | wired_pins | power_pin_keys

    pcv_issues = []
    uncovered = sorted(all_pins - assigned_pins)
    if uncovered:
        pcv_issues.append(ValidationIssue(
            code="PCV001",
            severity="info",
            stage="routing",
            message=f"{len(uncovered)} pin(s) not assigned to any net or wire: {', '.join(uncovered[:8])}"
                     f"{'...' if len(uncovered) > 8 else ''}",
        ))
    covered_no_wire = sorted(net_pins - wired_pins - power_pin_keys)
    if covered_no_wire:
        pcv_issues.append(ValidationIssue(
            code="PCV002",
            severity="info",
            stage="routing",
            message=f"{len(covered_no_wire)} pin(s) in netlist but missing physical wire: "
                     f"{', '.join(covered_no_wire[:8])}{'...' if len(covered_no_wire) > 8 else ''}",
        ))

    for iss in pcv_issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    # ── 4. Targeted ERC re-route ────────────────────────────────────
    # If affected_nets is specified, only re-route those nets and preserve existing traces
    erc_affected = state.get("_erc_affected_nets", [])
    preserve_existing = bool(erc_affected)
    existing_traces = state.get("wire_paths", [])

    if preserve_existing and erc_affected:
        affected_set = set(erc_affected)
        # Keep traces for unaffected nets
        preserved = [t for t in existing_traces if t.get("net", "") not in affected_set]
        # Build targeted netlist: only connections in affected nets
        targeted_netlist = [c for c in working_netlist if c.get("net", "") in affected_set]
        _emit(config, "agent:log", {
            "message": f"  Targeted re-route: {len(targeted_netlist)} connections in {len(affected_set)} affected net(s), "
                       f"{len(preserved)} existing traces preserved"
        })
    else:
        preserved = []
        targeted_netlist = working_netlist

    # ── 5. Route traces ─────────────────────────────────────────────
    route_netlist = targeted_netlist if preserve_existing else working_netlist
    raw_traces, dropped_pairs = engine.route_traces(route_netlist, pin_matrix)

    # Log each dropped pair with reason for observability
    if dropped_pairs:
        for src_ref, tgt_ref in dropped_pairs:
            _emit(config, "agent:log", {
                "message": f"  ⚠ Dropped wire: {src_ref} ↔ {tgt_ref} (routing failed)"
            })

    # ── 5b. Placement repair + re-route for dropped pairs ───────────
    # Move satellites closer to their partners and try routing again.
    n_retried = 0
    retry_traces = []
    retry_dropped = []
    if dropped_pairs and not preserve_existing:
        n_moved = engine._repair_placement_for_routing(dropped_pairs)
        if n_moved > 0:
            _emit(config, "agent:log", {
                "message": f"  Placement repair: nudged {n_moved} component(s) closer to fix dropped wires"
            })
            # Build a netlist of just the dropped connections for re-routing
            dropped_netlist = [
                c for c in working_netlist
                if (c["source"].split(":")[0], c["target"].split(":")[0]) in
                   [(a, b) for a, b in dropped_pairs] or
                   (c["target"].split(":")[0], c["source"].split(":")[0]) in
                   [(a, b) for a, b in dropped_pairs]
            ]
            retry_traces, retry_dropped = engine.route_traces(dropped_netlist, pin_matrix)
            n_retried = len(retry_traces)
            # Update placements after nudge
            placements = engine.get_placements()

    # Relax max wire length on ERC re-routing passes — long wires are
    # better than unconnected pins that fail ERC.
    # Aligned with layout_engine MAX_WIRE_MANHATTAN * 1.5 (300mm).
    MAX_WIRE_LEN = 300.0
    clean_traces = list(preserved) if preserve_existing else []
    n_dropped = 0
    n_len_dropped = 0
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
            if total_len > MAX_WIRE_LEN:
                ok = False
                break
        if not ok:
            n_dropped += 1
            if total_len > 150.0:
                n_len_dropped += 1
            continue
        clean_traces.append(tr)

    # Merge retry traces (from placement repair) into clean_traces
    for tr in retry_traces:
        path = tr.get("path", [])
        if len(path) < 2:
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
            if total_len > MAX_WIRE_LEN:
                ok = False
                break
        if ok:
            clean_traces.append(tr)
            n_retried += 1  # count only successful retries

    # Final dropped_pairs: original minus recovered
    recovered = len([t for t in retry_traces if len(t.get("path", [])) >= 2])

    total_wire = sum(
        sum(abs(p[i+1]["x"] - p[i]["x"]) + abs(p[i+1]["y"] - p[i]["y"])
            for i in range(len(p) - 1))
        for p in [tr["path"] for tr in clean_traces if tr.get("path")]
    ) if clean_traces else 0.0

    _emit(config, "agent:log", {
        "message": (
            f"  Routing: {len(clean_traces)} wires, "
            f"{n_dropped} dropped ({n_len_dropped} length, "
            f"{n_dropped - n_len_dropped} other)"
            f" (+{len(dropped_pairs)} pairs), "
            f"{recovered}/{len(dropped_pairs)} recovered via placement repair, "
            f"{total_wire:.1f}mm total"
        )
    })

    # ── 4. Generate power labels ────────────────────────────────────
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

    # ── 5. Emit layout_ready ────────────────────────────────────────
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": clean_traces,
        "power_labels": power_labels,
        "netlist": working_netlist,
        "power_pins": power_pins,
    })

    suffix = " (ERC repair re-route)" if erc_pending else ""
    emit_tool_event(config, "Routing", "completed",
                    f"{len(clean_traces)} wires routed{suffix}")
    emit_assistant_message(
        config,
        f"Routing complete — {len(clean_traces)} wires drawn{suffix}."
    )

    result = {
        "component_placements": placements,
        "wire_paths": clean_traces,
        "power_labels": power_labels,
        "netlist": working_netlist,
        "_dropped_pairs": dropped_pairs,
        "_validation_issues": state.get("_validation_issues", []) + [i.to_dict() for i in pcv_issues],
    }
    # The ERC loop: always increment retries and clear pending connections
    # when we're inside an ERC → repair → routing iteration.
    if state.get("_erc_results"):
        result["_erc_retries"] = erc_retries + 1
        result["_erc_pending_connections"] = []

    return result
