from agent.utils import _emit, GND_NET_NAMES, POWER_NET_NAMES, POWER_ETYPES, _merge_net


# Voltage rail separation: these nets must NEVER be merged
_VOLTAGE_RAILS = {
    "VBUS": 5.0, "VUSB": 5.0, "VSYS": 5.0,
    "3V3": 3.3, "3.3V": 3.3, "VCC": 3.3, "VDD": 3.3,
    "1V8": 1.8, "1.8V": 1.8,
    "1V2": 1.2, "1.2V": 1.2,
}

# Groups of rails that are electrically equivalent (same voltage)
_EQUIVALENT_RAILS = {
    frozenset({"VBUS", "VUSB", "VSYS"}),  # 5V rails
    frozenset({"3V3", "3.3V", "VCC", "VDD"}),  # 3.3V rails
    frozenset({"1V8", "1.8V"}),  # 1.8V rails
    frozenset({"1V2", "1.2V"}),  # 1.2V rails
}


def _are_rails_compatible(name1: str, name2: str) -> bool:
    """Check if two power net names are electrically compatible (same voltage)."""
    n1 = name1.upper().lstrip("+")
    n2 = name2.upper().lstrip("+")
    if n1 == n2:
        return True
    for group in _EQUIVALENT_RAILS:
        if n1 in group and n2 in group:
            return True
    # If neither is a known rail, allow merge (unknown compatibility)
    if n1 not in _VOLTAGE_RAILS and n2 not in _VOLTAGE_RAILS:
        return True
    # One is known, one isn't — allow but warn
    return False


def _safe_merge_net(nets: list, name: str, new_pins: list, emit_fn=None) -> bool:
    """Merge net with voltage rail separation check.
    Returns True if merge succeeded, False if blocked."""
    target_name = name.upper().lstrip("+")
    for n in nets:
        existing_name = n["net"].upper().lstrip("+")
        if existing_name == target_name:
            n["pins"].extend(p for p in new_pins if p not in n["pins"])
            return True
        # Check if merging would connect different voltage rails
        if target_name in _VOLTAGE_RAILS and existing_name in _VOLTAGE_RAILS:
            if not _are_rails_compatible(target_name, existing_name):
                if emit_fn:
                    emit_fn("agent:log", {
                        "message": f"  BLOCKED: Cannot merge {name} into {n['net']} — different voltage rails"
                    })
                return False
            # Compatible rails — merge into existing net (keep its name)
            n["pins"].extend(p for p in new_pins if p not in n["pins"])
            if emit_fn:
                emit_fn("agent:log", {
                    "message": f"  Merged {name} into {n['net']} (compatible voltage rails)"
                })
            return True
    nets.append({"net": name, "pins": list(new_pins)})
    return True


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
                _safe_merge_net(nets, "GND", [key], lambda k, v: _emit(config, k, v))
                used_pins.add(key)
            elif pname in POWER_NET_NAMES:
                net_name = pname.lstrip("+")
                power_pins.append({"pin": key, "net": net_name})
                _safe_merge_net(nets, net_name, [key], lambda k, v: _emit(config, k, v))
                used_pins.add(key)
            elif p_pin.get("etype") in POWER_ETYPES and pname and pname != "~":
                net_name = pname.lstrip("+")
                power_pins.append({"pin": key, "net": net_name})
                _safe_merge_net(nets, net_name, [key], lambda k, v: _emit(config, k, v))
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
