"""Routing node — runs wire routing using existing placements.

Handles ERC repair attach requests by merging pending connections
into the netlist before routing. Never re-runs placement.
"""

from agent.placement.blocks_v2 import _prepare_components, _get_comp_ref
from agent.routing import route_traces, _snap
from agent.routing.api import repair_placement_for_routing
from agent.validation import ValidationIssue
from agent.utils import _emit, emit_assistant_message, emit_tool_event, emit_thought, emit_tool_call, emit_tool_end, emit_step
from uuid import uuid4


def _find_attachment_buddy(pin: str, net_name: str,
                           netlist: list, nets: list,
                           pin_matrix: dict,
                           engine_components: list,
                           power_pins: list | None = None) -> str | None:
    net_pins = []
    for n in nets:
        if n["net"] == net_name:
            net_pins = n["pins"]
            break

    if not net_pins:
        return None

    degree: dict[str, int] = {}
    for c in netlist:
        s, t = c["source"], c["target"]
        degree[s] = degree.get(s, 0) + 1
        degree[t] = degree.get(t, 0) + 1

    candidates = [p for p in net_pins if p != pin and degree.get(p, 0) >= 1]

    if not candidates and power_pins:
        for pp in power_pins:
            if pp["net"].upper() == net_name.upper() and pp["pin"] != pin:
                if pp["pin"] in net_pins:
                    candidates.append(pp["pin"])

    if not candidates:
        return None

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

    route_id = uuid4().hex[:8]
    emit_tool_call(config, route_id, "Wire Routing", "running")
    emit_thought(config, "Routing wires on the schematic...")
    msg = "Re-routing with ERC repair connections..." if erc_pending else "Routing wires..."
    emit_assistant_message(config, msg)
    emit_tool_event(config, "Routing", "running",
                    "Re-routing..." if erc_pending else "Routing wires...")

    # ── 1. Build component list and restore placements ──────────────
    components = []
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        components.append({
            "ref_des": ref_des,
            "ops": ops,
            "category": comp["category"],
            "id_str": comp.get("id_str", ""),
            "for_component": comp.get("for_component", ""),
        })

    components = _prepare_components(components)

    if not components:
        emit_tool_event(config, "Routing", "failed", "No components could be routed.")
        return {}

    for c in components:
        match = next((p for p in placements if p["ref_des"] == c["ref_des"]), None)
        if match:
            c["x"] = match["x"]
            c["y"] = match["y"]
            c["rotation"] = match.get("rotation", 0)

    # ── 2. Merge ERC pending connections into netlist ───────────────
    emit_step(config, route_id, "Merging ERC connections..." if erc_pending else "Building netlist...", "running")
    working_netlist = list(netlist)
    if erc_pending:
        for req in erc_pending:
            pin_key = req["pin"]
            net_name = req["net"]
            stale = [c for c in working_netlist
                     if pin_key in (c["source"], c["target"])]
            for s in stale:
                working_netlist.remove(s)
            buddy = _find_attachment_buddy(pin_key, net_name,
                                           working_netlist, nets,
                                           pin_matrix, components,
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

    # Filter pins that are intentionally unconnected (NC, empty name, etc.)
    _INTENTIONALLY_UNCONNECTED = frozenset({"NC", "NO_CONNECT", "NO_CONNECTION", "N/C", "NOCONNECT", ""})
    uncovered_raw = sorted(all_pins - assigned_pins)
    uncovered = [
        p for p in uncovered_raw
        if pin_matrix.get(p, {}).get("name", "").strip().upper() not in _INTENTIONALLY_UNCONNECTED
    ]

    pcv_issues = []
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
    erc_affected = state.get("_erc_affected_nets", [])
    preserve_existing = bool(erc_affected)
    existing_traces = state.get("wire_paths", [])

    if preserve_existing and erc_affected:
        affected_set = set(erc_affected)
        preserved = [t for t in existing_traces if t.get("net", "") not in affected_set]
        targeted_netlist = [c for c in working_netlist if c.get("net", "") in affected_set]
        _emit(config, "agent:log", {
            "message": f"  Targeted re-route: {len(targeted_netlist)} connections in {len(affected_set)} affected net(s), "
                       f"{len(preserved)} existing traces preserved"
        })
    else:
        preserved = []
        targeted_netlist = working_netlist

    # ── 5. Route traces ─────────────────────────────────────────────
    emit_step(config, route_id, "Routing wires...", "running")
    route_netlist = targeted_netlist if preserve_existing else working_netlist
    raw_traces, dropped_pairs = route_traces(components, route_netlist, pin_matrix,
                                             erc_retries=erc_retries)

    if dropped_pairs:
        for src_ref, tgt_ref in dropped_pairs:
            _emit(config, "agent:log", {
                "message": f"  \u26a0 Dropped wire: {src_ref} \u2194 {tgt_ref} (routing failed)"
            })

    # ── 5b. Placement repair + re-route for dropped pairs ───────────
    n_retried = 0
    retry_traces = []
    n_retry_dropped = 0
    if dropped_pairs and not preserve_existing:
        n_moved = repair_placement_for_routing(components, dropped_pairs)
        if n_moved > 0:
            _emit(config, "agent:log", {
                "message": f"  Placement repair: nudged {n_moved} component(s) closer to fix dropped wires"
            })
            dropped_netlist = [
                c for c in working_netlist
                if (c["source"].split(":")[0], c["target"].split(":")[0]) in
                   [(a, b) for a, b in dropped_pairs] or
                   (c["target"].split(":")[0], c["source"].split(":")[0]) in
                   [(a, b) for a, b in dropped_pairs]
            ]
            retry_traces, n_retry_dropped = route_traces(components, dropped_netlist, pin_matrix,
                                                         erc_retries=erc_retries)
            placements = [{"ref_des": c["ref_des"], "x": c["x"], "y": c["y"],
                           "rotation": c.get("rotation", 0.0)} for c in components]

    MAX_WIRE_LEN = 300.0 * (1 + erc_retries * 0.5)
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
            n_retried += 1

    recovered = n_retried

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
            f" (+{len(dropped_pairs)} pairs, "
            f"{n_retry_dropped} re-route drops), "
            f"{recovered}/{len(dropped_pairs)} recovered via placement repair, "
            f"{total_wire:.1f}mm total"
        )
    })

    # ── 6. Generate power labels ────────────────────────────────────
    power_labels = []
    for pp in power_pins:
        pin_obj = pin_matrix.get(pp["pin"])
        if not pin_obj:
            continue
        ref = pp["pin"].split(":")[0]
        comp = _get_comp_ref(ref, components)
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

    # ── 7. Emit layout_ready ────────────────────────────────────────
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
    emit_tool_end(config, route_id, f"Routed {len(clean_traces)} wires{suffix}",
                   status="completed" if n_dropped == 0 else "failed")
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
    if state.get("_erc_results"):
        result["_erc_retries"] = erc_retries + 1
        result["_erc_pending_connections"] = []
        # Save error count for no-progress detection in _route_after_erc
        erc_errors = state["_erc_results"].get("errors", [])
        result["_prev_erc_error_count"] = len(erc_errors)

    return result
