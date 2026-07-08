from agent.validation import ValidationIssue
from agent.utils import _emit


def structural_net_repair_node(state, config):
    issues = state.get("_validation_issues", [])
    nets = state.get("nets", [])
    netlist = state.get("netlist", [])
    pin_matrix = state.get("pin_matrix", {})
    power_pins = state.get("power_pins", [])

    snv004_issues = [i for i in issues if i.get("code") == "SNV004"]
    if not snv004_issues:
        _emit(config, "agent:log", {"message": "  Structural net repair: no fixable issues found"})
        return {}

    import copy
    from agent.tools import fetch_sexpr as _fetch_sexpr
    from agent.utils import _parse_sexpr_to_ops, _extract_pins_from_ops, _create_pwr_flag_component

    new_power_pins = list(power_pins)
    selected = state.get("selected_components", [])
    comp_ops = state.get("component_ops", {})
    pm = dict(pin_matrix)

    try:
        sexpr = _fetch_sexpr("power:PWR_FLAG")
        flag_ops = _parse_sexpr_to_ops(sexpr, "power")
        flag_pin_raw = _extract_pins_from_ops(flag_ops, "_PWRF")
    except Exception:
        _emit(config, "agent:log", {"message": "  SNV repair: failed to fetch PWR_FLAG symbol"})
        return {}

    n_fixed = 0
    for iss in snv004_issues:
        net_name = iss.get("net", "")
        if not net_name:
            continue

        already_has = any(pp.get("net", "").upper() == net_name.upper() for pp in new_power_pins)
        if already_has:
            continue

        n_fixed += 1
        fc = _create_pwr_flag_component(
            net_name, n_fixed, flag_ops, flag_pin_raw,
            f"PWR_FLAG: net {net_name} has no driver (SNV004 repair)",
        )
        selected.append(fc["component"])
        comp_ops[fc["ref"]] = copy.deepcopy(fc["comp_op"])
        pkey, pval = fc["pin_entry"]
        pm[pkey] = pval
        new_power_pins.append(fc["power_pin_entry"])

        for net_obj in nets:
            if net_obj["net"] == net_name:
                net_obj.setdefault("pins", [])
                new_pin = f"{fc['ref']}:1"
                if new_pin not in net_obj["pins"]:
                    net_obj["pins"].append(new_pin)
                break

    if n_fixed:
        _emit(config, "agent:log", {
            "message": f"  SNV repair: injected PWR_FLAG on {n_fixed} undriven power net(s)"
        })

    return {
        "selected_components": selected,
        "component_ops": comp_ops,
        "pin_matrix": pm,
        "power_pins": new_power_pins,
        "nets": nets,
    }
