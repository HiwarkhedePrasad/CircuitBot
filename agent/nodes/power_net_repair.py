from agent.utils import _emit, GND_NET_NAMES, POWER_NET_NAMES, POWER_ETYPES, _merge_net


def power_net_repair_node(state, config):
    nets = list(state.get("nets", []))
    netlist = list(state.get("netlist", []))
    pin_matrix = state.get("pin_matrix", {})
    power_pins = list(state.get("power_pins", []))
    placements = state.get("component_placements", [])
    selected = list(state.get("selected_components", []))

    used_pins = set()
    for net in nets:
        for p in net.get("pins", []):
            used_pins.add(p)
    for pp in power_pins:
        used_pins.add(pp["pin"])

    all_refs = {c["ref_des"] for c in selected}
    connected_refs = set()
    for conn in netlist:
        connected_refs.add(conn["source"].split(":")[0])
        connected_refs.add(conn["target"].split(":")[0])
    for pp in power_pins:
        connected_refs.add(pp["pin"].split(":")[0])

    orphans = sorted(all_refs - connected_refs)
    if not orphans:
        _emit(config, "agent:log", {"message": "  Power net repair: no orphan components found"})
        return {}

    _emit(config, "agent:log", {
        "message": f"  Power net repair: attaching {len(orphans)} orphan component(s) — {', '.join(orphans)}"
    })

    for ref in orphans:
        orphan_pins = [k for k in pin_matrix if k.split(":")[0] == ref and k not in used_pins]
        is_2pin_passive = len([k for k in pin_matrix if k.startswith(f"{ref}:")]) <= 2
        for key in orphan_pins:
            p_pin = pin_matrix[key]
            pname = p_pin.get("name", "").upper()
            if pname in GND_NET_NAMES or pname == "EPAD" or pname == "EP":
                power_pins.append({"pin": key, "net": "GND"})
                _merge_net(nets, "GND", [key])
                used_pins.add(key)
            elif pname in POWER_NET_NAMES:
                net_name = pname.lstrip("+")
                power_pins.append({"pin": key, "net": net_name})
                _merge_net(nets, net_name, [key])
                used_pins.add(key)
            elif p_pin.get("etype") in POWER_ETYPES and pname and pname != "~":
                net_name = pname.lstrip("+")
                power_pins.append({"pin": key, "net": net_name})
                _merge_net(nets, net_name, [key])
                used_pins.add(key)
            elif is_2pin_passive:
                already_gnd = any(
                    pp["pin"].startswith(f"{ref}:") and pp["net"] == "GND"
                    for pp in power_pins
                )
                if not already_gnd:
                    power_pins.append({"pin": key, "net": "GND"})
                    _merge_net(nets, "GND", [key])
                    used_pins.add(key)
                    _emit(config, "agent:log", {
                        "message": f"  Passive orphan rescue: {key} -> GND"
                    })

    _emit(config, "agent:log", {
        "message": f"  Power net repair: {len(power_pins)} total power pins, {len(nets)} nets"
    })

    return {
        "nets": nets,
        "power_pins": power_pins,
        "netlist": netlist,
    }
