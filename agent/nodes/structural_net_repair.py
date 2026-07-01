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
    from agent.utils import _parse_sexpr_to_ops, _extract_pins_from_ops

    new_power_pins = list(power_pins)
    selected = state.get("selected_components", [])
    comp_ops = state.get("component_ops", {})
    pm = dict(pin_matrix)
    modified = False

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

        # Check if PWR_FLAG already exists for this net
        already_has = any(pp.get("net", "").upper() == net_name.upper() for pp in new_power_pins)
        if already_has:
            continue

        i = n_fixed + 1
        ref = f"#FLG{i:02d}"
        selected.append({
            "id_str": "power:PWR_FLAG",
            "ref_des": ref,
            "category": "Power_Management",
            "description": f"Power flag for {net_name}",
            "footprint": "", "pads": [],
            "justification": f"PWR_FLAG: net {net_name} has no driver (SNV004 repair)",
            "datasheet_text": "",
        })
        comp_ops[ref] = copy.deepcopy(flag_ops)
        if flag_pin_raw:
            pk = list(flag_pin_raw.keys())[0]
            pv = flag_pin_raw[pk]
            adj_key = f"{ref}:{pv['pin_num']}"
            adj_pv = dict(pv)
            adj_pv["ref_des"] = ref
            pm[adj_key] = adj_pv
            new_power_pins.append({"pin": adj_key, "net": net_name})
        else:
            pm[f"{ref}:1"] = {
                "x": 0, "y": 0, "name": "",
                "num": "1", "pin_num": "1",
                "ref_des": ref, "angle": 90, "etype": "power_out",
            }
            new_power_pins.append({"pin": f"{ref}:1", "net": net_name})

        for net_obj in nets:
            if net_obj["net"] == net_name:
                net_obj.setdefault("pins", [])
                new_pin = f"{ref}:1"
                if new_pin not in net_obj["pins"]:
                    net_obj["pins"].append(new_pin)
                break

        n_fixed += 1

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
