from agent.validation import ValidationIssue
from agent.utils import _emit, GND_NET_NAMES, POWER_NET_NAMES


def structural_net_validate_node(state, config):
    issues = []
    nets = state.get("nets", [])
    netlist = state.get("netlist", [])
    pin_matrix = state.get("pin_matrix", {})

    # SNV001: Net with single pin (unconnected stub)
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if len(pins) < 2 and name.upper() not in GND_NET_NAMES:
            issues.append(ValidationIssue(
                code="SNV001",
                severity="warning",
                stage="structural_net_validate",
                message=f"Net '{name}' has only {len(pins)} pin(s)",
                net=name,
            ))

    # SNV002: Netlist connection referencing non-existent pin
    existing_pins = set(pin_matrix.keys())
    for conn in netlist:
        s = conn.get("source", "")
        t = conn.get("target", "")
        if s and s not in existing_pins:
            issues.append(ValidationIssue(
                code="SNV002",
                severity="error",
                stage="structural_net_validate",
                message=f"Connection references non-existent source pin: {s}",
                component=s.split(":")[0],
                pin=s,
            ))
        if t and t not in existing_pins:
            issues.append(ValidationIssue(
                code="SNV002",
                severity="error",
                stage="structural_net_validate",
                message=f"Connection references non-existent target pin: {t}",
                component=t.split(":")[0],
                pin=t,
            ))

    # SNV003: Net with mismatched pin etypes (e.g. power_out connected to power_out)
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        etypes = {}
        for p in pins:
            pin = pin_matrix.get(p, {})
            etype = pin.get("etype", "passive")
            etypes.setdefault(etype, []).append(p)
        if len(etypes) > 1:
            has_power_out = "power_out" in etypes
            only_power = all(
                e in ("power_in", "power_out", "passive") for e in etypes
            )
            if has_power_out and only_power:
                continue
        if "output" in etypes and "input" not in etypes and "bidirectional" not in etypes and "passive" not in etypes and "power_in" not in etypes:
            if name.upper() not in GND_NET_NAMES and name.upper() not in POWER_NET_NAMES:
                issues.append(ValidationIssue(
                    code="SNV003",
                    severity="warning",
                    stage="structural_net_validate",
                    message=f"Net '{name}' has output pin(s) but no input or bidirectional — may be undriven",
                    net=name,
                ))

    # SNV004: Power net with only power_in pins (no driver)
    for net in nets:
        name = net.get("net", "")
        pins = net.get("pins", [])
        if name.upper() in GND_NET_NAMES:
            continue
        if name.upper() in POWER_NET_NAMES or name.upper().lstrip("+") in {"3V3", "5V", "1V8", "1V2", "3.3V"}:
            has_driver = any(
                pin_matrix.get(p, {}).get("etype", "") in ("power_out", "output")
                for p in pins
            )
            if not has_driver:
                issues.append(ValidationIssue(
                    code="SNV004",
                    severity="warning",
                    stage="structural_net_validate",
                    message=f"Power net '{name}' has no driver pin (power_out or output)",
                    net=name,
                    fixable=True,
                ))

    for iss in issues:
        _emit(config, "agent:log", {"message": f"  {iss.code}: {iss.message}"})

    return {"_validation_issues": state.get("_validation_issues", []) + [i.to_dict() for i in issues]}
