"""Routing node — generates ConnectionRecords from the classified netlist.

Replaced the explicit-wire autorouter with a net-label-first architecture:
short wires for local connections, net labels / global labels for buses
and power rails.
"""

from __future__ import annotations

from uuid import uuid4

from agent.connection_graph import ConnectivityGraph
from agent.connection_emitter import emit_connections
from agent.connection_strategy import WIRE
from agent.placement.blocks_v2 import _prepare_components, _get_comp_ref


def _dedupe_issues(issues: list[dict]) -> list[dict]:
    """Deduplicate validation issues by message to prevent unbounded growth in ERC loops."""
    seen = set()
    deduped = []
    for issue in issues:
        msg = issue.get("message", "")
        if msg and msg not in seen:
            seen.add(msg)
            deduped.append(issue)
        elif not msg:
            deduped.append(issue)
    return deduped
from agent.routing.geometry import _absolute_pin_position, _pin_direction, _snap
from agent.routing.constants import PIN_STUB_LEN
from agent.validation import ValidationIssue
from agent.utils import _emit, emit_assistant_message, emit_tool_event, emit_thought, emit_tool_call, emit_tool_end, emit_step


def _stub_end_for_power(pin: dict, component: dict) -> tuple[float, float]:
    from agent.routing.geometry import _stub_point
    pos = _absolute_pin_position(pin, component)
    direction = _pin_direction(pin)
    return _stub_point(pos[0], pos[1], direction, PIN_STUB_LEN)


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
    emit_tool_call(config, route_id, "Connection Generation", "running")
    emit_thought(config, "Generating connections on the schematic...")
    msg = "Re-connecting with ERC repair connections..." if erc_pending else "Generating connections..."
    emit_assistant_message(config, msg)
    emit_tool_event(config, "Connection", "running",
                    "Re-connecting..." if erc_pending else "Generating connections...")

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
        emit_tool_event(config, "Connection", "failed", "No components to connect.")
        return {}

    for c in components:
        match = next((p for p in placements if p["ref_des"] == c["ref_des"]), None)
        if match:
            c["x"] = match["x"]
            c["y"] = match["y"]
            c["rotation"] = match.get("rotation", 0)

    # ── 2. Build placements dict for span estimation ────────────────
    placements_dict: dict[str, dict] = {}
    for p in placements:
        placements_dict[p["ref_des"]] = p
    for c in components:
        ref = c["ref_des"]
        if ref not in placements_dict:
            placements_dict[ref] = {"x": c.get("x", 0), "y": c.get("y", 0)}

    # ── 3. Merge ERC pending connections into netlist/nets ──────────
    emit_step(config, route_id, "Merging ERC connections..." if erc_pending else "Analyzing netlist...", "running")
    working_netlist = list(netlist)
    working_nets = [dict(n) for n in nets]
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
                # Also add to working_nets if the net exists
                for wn in working_nets:
                    if wn["net"] == net_name:
                        if pin_key not in wn.get("pins", []):
                            wn.setdefault("pins", []).append(pin_key)
                            wn.setdefault("pins", []).append(buddy)
                        break

    # ── 4. Build connectivity graph and emit connection records ────
    emit_step(config, route_id, "Building connectivity graph...", "running")
    graph = ConnectivityGraph(working_nets, pin_matrix, components, placements_dict)
    connection_records = emit_connections(graph)

    # ── 5. Convert connection records to backward-compat formats ───
    wire_paths = []
    net_labels = []
    power_labels = []

    for cr in connection_records:
        net_name = cr["net"]
        geo = cr["geometry"]

        if cr["type"] == WIRE and geo.get("wire_path"):
            path_pts = [{"x": p[0], "y": p[1]} for p in geo["wire_path"]]
            wire_paths.append({
                "source": cr["source_pin"],
                "target": cr["target_pin"],
                "net": net_name,
                "path": path_pts,
            })
        else:
            # GLOBAL connection records are for power nets and are handled
            # entirely by the power_labels section below (5b) which produces
            # both stub wires and global labels.  Skipping them here avoids
            # duplicate labels (section c) and prevents EV001 when the stub
            # wire from _emit_global is zero-length after snapping.
            if cr["type"] == "global":
                continue
            for sx, sy, ex, ey in geo.get("stub_wires", []):
                wire_paths.append({
                    "source": cr["source_pin"],
                    "target": "",
                    "net": net_name,
                    "path": [{"x": sx, "y": sy}, {"x": ex, "y": ey}],
                })

            for lpos in geo.get("label_positions", []):
                net_labels.append({
                    "type": cr["type"],
                    "net": net_name,
                    "pin": cr["source_pin"],
                    "at": {"x": lpos[0], "y": lpos[1]},
                })

    # ── 5b. Power labels (global label shorthand for non-GLOBAL nets) ─
    for pp in power_pins:
        pin_obj = pin_matrix.get(pp["pin"])
        if not pin_obj:
            continue
        ref = pp["pin"].split(":")[0]
        comp = _get_comp_ref(ref, components)
        if not comp:
            continue
        ax, ay = _absolute_pin_position(pin_obj, comp)
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

    # ── 6. Pin-coverage validation (PCV) ────────────────────────────
    all_pins = set(pin_matrix.keys())
    net_pins = set()
    for n in working_nets:
        for p in n.get("pins", []):
            net_pins.add(p)
    connected_pins = set()
    for cr in connection_records:
        if cr["source_pin"]:
            connected_pins.add(cr["source_pin"])
        if cr["target_pin"]:
            connected_pins.add(cr["target_pin"])
    for pp in power_pins:
        connected_pins.add(pp["pin"])

    _INTENTIONALLY_UNCONNECTED = frozenset({"NC", "NO_CONNECT", "NO_CONNECTION", "N/C", "NOCONNECT", ""})
    uncovered_raw = sorted(all_pins - connected_pins)
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
            message=f"{len(uncovered)} pin(s) not covered by any connection record: {', '.join(uncovered[:8])}"
                     f"{'...' if len(uncovered) > 8 else ''}",
        ))
    unattached_labels = [
        nl for nl in net_labels
        if nl["type"] in ("label", "global") and nl["pin"] not in connected_pins
    ]
    if unattached_labels:
        pcv_issues.append(ValidationIssue(
            code="PCV002",
            severity="info",
            stage="routing",
            message=f"{len(unattached_labels)} label(s) unattached to a pin",
        ))

    for iss in pcv_issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    # ── 7. Emit layout_ready ────────────────────────────────────────
    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": wire_paths,
        "power_labels": power_labels,
        "netlist": working_netlist,
        "power_pins": power_pins,
        "net_labels": net_labels,
    })

    suffix = " (ERC repair reconnect)" if erc_pending else ""
    n_wires = sum(1 for cr in connection_records if cr["type"] == WIRE)
    n_labels = sum(1 for cr in connection_records if cr["type"] in ("label", "global"))
    emit_tool_event(config, "Connection", "completed",
                    f"{n_wires} wires, {n_labels} labels{suffix}")
    emit_tool_end(config, route_id,
                  f"Generated {n_wires} wire(s) and {n_labels} label(s){suffix}",
                  status="completed" if not uncovered else "warning")
    emit_assistant_message(
        config,
        f"Connectivity complete — {n_wires} wire(s), {n_labels} net label(s){suffix}."
    )

    result = {
        "component_placements": placements,
        "connection_records": connection_records,
        "wire_paths": wire_paths,
        "net_labels": net_labels,
        "power_labels": power_labels,
        "netlist": working_netlist,
        "_validation_issues": _dedupe_issues(state.get("_validation_issues", []) + [i.to_dict() for i in pcv_issues]),
    }
    if state.get("_erc_results"):
        result["_erc_retries"] = erc_retries + 1
        result["_erc_pending_connections"] = []
        erc_errors = state["_erc_results"].get("errors", [])
        result["_prev_erc_error_count"] = len(erc_errors)

    return result
