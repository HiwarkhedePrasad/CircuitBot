"""Routing node — runs wire routing using existing placements.

Handles ERC repair attach requests by merging pending connections
into the netlist before routing. Never re-runs placement.
"""

from agent.layout_engine import BackendLayoutEngine, _snap
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

    # ── 3. Route traces ─────────────────────────────────────────────
    raw_traces, dropped_pairs = engine.route_traces(working_netlist, pin_matrix)

    # Relax max wire length on ERC re-routing passes — long wires are
    # better than unconnected pins that fail ERC.
    MAX_WIRE_LEN = 300.0 if erc_pending else 150.0
    clean_traces = []
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
    }
    # The ERC loop: always increment retries and clear pending connections
    # when we're inside an ERC → repair → routing iteration.
    if state.get("_erc_results"):
        result["_erc_retries"] = erc_retries + 1
        result["_erc_pending_connections"] = []

    return result
