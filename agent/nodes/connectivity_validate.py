from agent.validation import ValidationIssue
from agent.utils import _emit, GND_NET_NAMES, POWER_NET_NAMES
from agent.routing.geometry import _absolute_pin_position
from agent.routing.path_utils import _is_orthogonal
from agent.routing.constants import GRID_SIZE, PIN_STUB_LEN


def _abs_pin_position(pin_key: str, pin_matrix: dict, placements: list[dict]) -> tuple[float, float] | None:
    ref = pin_key.split(":")[0] if ":" in pin_key else ""
    pin = pin_matrix.get(pin_key)
    if not pin:
        return None
    place = next((p for p in placements if p["ref_des"] == ref), None)
    if not place:
        return None
    comp = {
        "x": place.get("x", 0),
        "y": place.get("y", 0),
        "rotation": place.get("rotation", 0),
    }
    return _absolute_pin_position(pin, comp)


def _geometry_issues(wire_paths, pin_matrix, placements):
    issues = []
    for trace in wire_paths:
        source = trace.get("source", "")
        target = trace.get("target", "")
        path = trace.get("path", [])
        if len(path) < 2:
            continue
        actual = [(point.get("x"), point.get("y")) for point in path]
        if source:
            expected = _abs_pin_position(source, pin_matrix, placements)
            if expected and actual[0] != expected:
                issues.append(ValidationIssue(
                    code="PGV005", severity="warning", stage="connectivity_validate",
                    message=f"Wire for {source} does not start at its pin endpoint",
                    component=source.split(":")[0], pin=source, net=trace.get("net", ""),
                ))
        if target:
            expected = _abs_pin_position(target, pin_matrix, placements)
            if expected and actual[-1] != expected:
                issues.append(ValidationIssue(
                    code="PGV005", severity="warning", stage="connectivity_validate",
                    message=f"Wire for {target} does not end at its pin endpoint",
                    component=target.split(":")[0], pin=target, net=trace.get("net", ""),
                ))
        if not _is_orthogonal(actual):
            issues.append(ValidationIssue(
                code="PGV006", severity="warning", stage="connectivity_validate",
                message=f"Wire {source} to {target} contains a diagonal segment",
                component=source.split(":")[0], pin=source, net=trace.get("net", ""),
            ))
    return issues


def _net_label_issues(net_labels, pin_matrix, placements):
    issues = []
    for nl in net_labels:
        pin_key = nl.get("pin", "")
        at = nl.get("at", {})
        if not pin_key:
            continue
        expected = _abs_pin_position(pin_key, pin_matrix, placements)
        if expected:
            lx, ly = at.get("x", 0), at.get("y", 0)
            dx = abs(lx - expected[0])
            dy = abs(ly - expected[1])
            max_dist = max(GRID_SIZE * 2, PIN_STUB_LEN * 3)
            if dx > max_dist or dy > max_dist:
                issues.append(ValidationIssue(
                    code="PGV005", severity="warning", stage="connectivity_validate",
                    message=f"Net label '{nl.get('net', '')}' for {pin_key} is far from its pin",
                    component=pin_key.split(":")[0], pin=pin_key, net=nl.get("net", ""),
                ))
    return issues


def connectivity_validate_node(state, config):
    issues = []
    nets = state.get("nets", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    comps = state.get("selected_components", [])
    placements = state.get("component_placements", [])

    connection_records = state.get("connection_records", [])
    wired_pins = set()
    for t in state.get("wire_paths", []):
        src = t.get("source", "")
        tgt = t.get("target", "")
        if src:
            wired_pins.add(src)
        if tgt:
            wired_pins.add(tgt)
    for cr in connection_records:
        if cr.get("source_pin"):
            wired_pins.add(cr["source_pin"])
        if cr.get("target_pin"):
            wired_pins.add(cr["target_pin"])

    for conn in netlist:
        s, t = conn.get("source", ""), conn.get("target", "")
        if s and s not in wired_pins:
            issues.append(ValidationIssue(
                code="PGV001",
                severity="warning",
                stage="connectivity_validate",
                message=f"Pin {s} in netlist but missing connection record",
                component=s.split(":")[0],
                pin=s,
                net=conn.get("net", ""),
            ))
        if t and t not in wired_pins:
            issues.append(ValidationIssue(
                code="PGV001",
                severity="warning",
                stage="connectivity_validate",
                message=f"Pin {t} in netlist but missing connection record",
                component=t.split(":")[0],
                pin=t,
                net=conn.get("net", ""),
            ))

    for pp in power_pins:
        net = pp.get("net", "")
        if not net:
            issues.append(ValidationIssue(
                code="PGV002",
                severity="warning",
                stage="connectivity_validate",
                message=f"Power pin {pp.get('pin', '')} has no net assignment",
                component=pp.get("pin", "").split(":")[0],
                pin=pp.get("pin", ""),
            ))

    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if not pins:
            issues.append(ValidationIssue(
                code="PGV003",
                severity="error",
                stage="connectivity_validate",
                message=f"Net '{name}' exists but has no pins",
                net=name,
            ))

    for net in nets:
        name = net.get("net", "").upper()
        if name in GND_NET_NAMES or name in POWER_NET_NAMES:
            for p in net.get("pins", []):
                pin = pin_matrix.get(p, {})
                etype = pin.get("etype", "")
                if etype in ("output", "bidirectional") and name not in ("VOUT",):
                    issues.append(ValidationIssue(
                        code="PGV004",
                        severity="info",
                        stage="connectivity_validate",
                        message=f"Pin {p} ({pin.get('name', '')}) of type '{etype}' on power net '{name}'",
                        component=p.split(":")[0],
                        pin=p,
                        net=name,
                    ))

        if name in GND_NET_NAMES:
            for p in net.get("pins", []):
                pin = pin_matrix.get(p, {})
                etype = pin.get("etype", "")
                if etype == "power_out":
                    issues.append(ValidationIssue(
                        code="PGV004",
                        severity="warning",
                        stage="connectivity_validate",
                        message=f"Pin {p} ({pin.get('name', '')}) type 'power_out' on GND net — possible ERC violation",
                        component=p.split(":")[0],
                        pin=p,
                        net=name,
                    ))

    issues.extend(_geometry_issues(state.get("wire_paths", []), pin_matrix, placements))
    issues.extend(_net_label_issues(state.get("net_labels", []), pin_matrix, placements))

    for iss in issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    return {"_validation_issues": state.get("_validation_issues", []) + [i.to_dict() for i in issues]}
