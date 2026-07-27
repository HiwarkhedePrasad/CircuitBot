"""ERC repair node — adds missing wire attachments flagged by KiCad ERC.

Operates only on pins already present in the existing nets dict.
Never invents net intent or guesses by pin name.
"""

from agent.utils import _emit


def schematic_repair_node(state, config):
    erc = state.get("_erc_results", {})
    if not erc:
        return {}

    fixable = erc.get("fixable", [])
    if not fixable:
        return {}

    nets = state.get("nets", [])
    netlist = state.get("netlist", [])
    traces = state.get("wire_paths", [])
    power_pins = state.get("power_pins", [])

    # Determine which pins actually have PHYSICAL wires on the schematic
    # (checking wire_paths, not the abstract netlist — a pin can be in netlist
    #  yet still lack a physical wire if the routing engine dropped it)
    wired_pins: set[str] = set()
    for t in traces:
        src = t.get("source", "")
        tgt = t.get("target", "")
        if src:
            wired_pins.add(src)
        if tgt:
            wired_pins.add(tgt)

    pending = []      # {pin, net} — wiring requests for the routing node
    add_to_power = [] # extra PWR_FLAG entries
    skipped = []      # pins we can't fix (not in any existing net)
    affected_nets: set[str] = set()  # net names affected by repair

    for f in fixable:
        pin_key = f.get("pin_key", "")
        ftype = f.get("type", "")
        if not pin_key:
            continue

        has_physical_wire = pin_key in wired_pins

        found_net = None
        for net_obj in nets:
            if pin_key in net_obj.get("pins", []):
                found_net = net_obj["net"]
                break
            net_pins_lower = [p.lower() for p in net_obj.get("pins", [])]
        no_connects = list(state.get("no_connects", []))
    add_to_nc = []

    for f in fixable:
        pin_key = f.get("pin_key", "")
        ftype = f.get("type", "")
        if not pin_key:
            continue

        has_physical_wire = pin_key in wired_pins

        found_net = None
        for net_obj in nets:
            if pin_key in net_obj.get("pins", []):
                found_net = net_obj["net"]
                break
            net_pins_lower = [p.lower() for p in net_obj.get("pins", [])]
            if pin_key.lower() in net_pins_lower:
                found_net = net_obj["net"]
                break

        if not found_net:
            if ftype in ("pin_not_connected", "unconnected_wire_endpoint") and pin_key not in no_connects and pin_key not in add_to_nc:
                add_to_nc.append(pin_key)
            else:
                skipped.append(pin_key)
            continue

        if ftype in ("pin_not_connected", "unconnected_wire_endpoint"):
            if has_physical_wire:
                continue
            pending.append({"pin": pin_key, "net": found_net})
            affected_nets.add(found_net)

        elif ftype == "power_pin_not_driven":
            if has_physical_wire:
                continue
            already_flagged = any(
                pp["pin"] == pin_key for pp in power_pins
            )
            if not already_flagged:
                add_to_power.append({"pin": pin_key, "net": found_net})

        elif ftype == "wire_dangling":
            if has_physical_wire:
                continue
            pending.append({"pin": pin_key, "net": found_net})
            affected_nets.add(found_net)

    _emit(config, "agent:log", {
        "message": (
            f"ERC repair: {len(pending)} attach request(s), "
            f"{len(add_to_power)} PWR_FLAG(s), "
            f"{len(add_to_nc)} no_connect(s), "
            f"{len(skipped)} skipped"
        )
    })

    new_power = list(power_pins)
    new_power.extend(add_to_power)

    new_nc = list(no_connects)
    new_nc.extend(add_to_nc)

    return {
        "_erc_pending_connections": pending,
        "_erc_affected_nets": sorted(affected_nets),
        "power_pins": new_power,
        "no_connects": new_nc,
    }
