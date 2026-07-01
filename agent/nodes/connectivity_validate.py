from agent.validation import ValidationIssue
from agent.utils import _emit, GND_NET_NAMES, POWER_NET_NAMES


def connectivity_validate_node(state, config):
    issues = []
    nets = state.get("nets", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])
    comps = state.get("selected_components", [])

    wired_pins = set()
    for t in state.get("wire_paths", []):
        src = t.get("source", "")
        tgt = t.get("target", "")
        if src:
            wired_pins.add(src)
        if tgt:
            wired_pins.add(tgt)

    # PGV001: Pin in netlist but no physical wire
    for conn in netlist:
        s, t = conn.get("source", ""), conn.get("target", "")
        if s and s not in wired_pins:
            issues.append(ValidationIssue(
                code="PGV001",
                severity="warning",
                stage="connectivity_validate",
                message=f"Pin {s} in netlist but missing physical wire",
                component=s.split(":")[0],
                pin=s,
                net=conn.get("net", ""),
            ))
        if t and t not in wired_pins:
            issues.append(ValidationIssue(
                code="PGV001",
                severity="warning",
                stage="connectivity_validate",
                message=f"Pin {t} in netlist but missing physical wire",
                component=t.split(":")[0],
                pin=t,
                net=conn.get("net", ""),
            ))

    # PGV002: Power pin without a net assignment
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

    # PGV003: Power net with no pins (empty net in nets list)
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

    # PGV004: Pin assigned to a power net but etype is bidirectional/output (unusual)
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

    for iss in issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    return {"_validation_issues": state.get("_validation_issues", []) + [i.to_dict() for i in issues]}
