import json

from agent.bus_checker import check_bus_topology
from agent.prompts import NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER
from agent.utils import (
    _emit, _emit_activity, _make_signal_batches, _merge_net, _generate_nets_fallback,
    _clean_json, _call_llm_with_tools,
    _is_gnd_net, _is_power_net, _canonical_signal_name, _resolve_hallucinated_pin,
    PIN_ALIASES, POWER_ETYPES, MAX_BATCH_PINS,
)


def netlist_node(state, config):
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})
    _emit_activity(config, "netlist", "Netlist Generation", "start")
    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": [], "nets": [], "power_pins": []}
    comps_desc = "\n".join(
        f'  {c["ref_des"]}: {c["id_str"]} ({c["category"]})'
        for c in comps
    )
    assigned = set()
    power_groups = {}
    for key, pin in pins.items():
        pname = pin.get("name", "").strip().upper()
        if _is_gnd_net(pname):
            power_groups.setdefault("GND", []).append(key)
            assigned.add(key)
        elif _is_power_net(pname):
            canon = pname.lstrip('+')
            if canon in ("VCC", "VDD"):
                canon = "3V3"
            power_groups.setdefault(canon, []).append(key)
            assigned.add(key)
    nets = [{"net": n, "pins": p} for n, p in power_groups.items()]
    _emit(config, "agent:log", {
        "message": f"  Power/GND pre-assigned deterministically: {len(assigned)} pins -> "
                   f"{', '.join(power_groups) or 'none'}"
    })
    signal_keys = [k for k in pins if k not in assigned]
    batches = _make_signal_batches(signal_keys, max_pins=MAX_BATCH_PINS)
    if len(batches) > 1:
        _emit(config, "agent:log", {
            "message": f"  Wiring {len(signal_keys)} signal pins in {len(batches)} batches"
        })
    for bi, batch_refs in enumerate(batches, 1):
        batch_keys = sorted(
            k for k in signal_keys
            if k.split(":")[0] in batch_refs and k not in assigned
        )
        if not batch_keys:
            continue
        _emit(config, "agent:thinking", {
            "message": f"Planning pin connections (batch {bi}/{len(batches)})..."
        })
        batch_refs_set = set(batch_refs)
        batch_comps_desc = "\n".join(
            f'  {c["ref_des"]}: {c["id_str"]} ({c["category"]})'
            for c in comps
            if c["ref_des"] in batch_refs_set
        )
        pins_desc = "\n".join(f'  {k}: pin_name="{pins[k]["name"]}"' for k in batch_keys)
        existing = ", ".join(n["net"] for n in nets) or "(none yet)"
        text = _call_llm_with_tools(NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER.format(
            prompt=state["prompt"],
            components_desc=batch_comps_desc,
            existing_nets=existing,
            pins_desc=pins_desc,
        ))
        text = _clean_json(text)
        try:
            batch_nets = json.loads(text) if text else []
        except json.JSONDecodeError:
            print(f"Batch {bi}: failed to parse nets JSON: {text[:200]}")
            batch_nets = []
        if not isinstance(batch_nets, list):
            continue
        n_dropped = 0
        batch_key_set = set(batch_keys)
        for net in batch_nets:
            if not isinstance(net, dict):
                continue
            name = str(net.get("net", "")).strip()
            raw = net.get("pins", [])
            if not name or not isinstance(raw, list):
                continue
            clean = []
            for p in raw:
                if p not in batch_key_set or p in assigned:
                    n_dropped += 1
                    continue
                assigned.add(p)
                clean.append(p)
            if clean:
                _merge_net(nets, name, clean)
        if n_dropped:
            _emit(config, "agent:log", {
                "message": f"  Batch {bi}: dropped {n_dropped} hallucinated/duplicate pin refs"
            })

    # ── Bus topology check ─────────────────────────────────────────
    nets, bus_warnings = check_bus_topology(nets, pins, comps)
    for w in bus_warnings:
        _emit(config, "agent:log", {"message": w})

    leftover = {k: pins[k] for k in pins if k not in assigned}
    if leftover:
        for net in _generate_nets_fallback(leftover, comps, nets):
            _merge_net(nets, net["net"], net["pins"])
        _emit(config, "agent:log", {
            "message": f"  Name-match fallback assigned {len(leftover)} leftover pins"
        })
    used_pins = set()
    valid_nets = []
    for net in nets:
        if not isinstance(net, dict):
            continue
        name = str(net.get("net", "")).strip()
        net_pins = net.get("pins", [])
        if not name or not isinstance(net_pins, list):
            continue
        clean = []
        for p in net_pins:
            if p not in pins:
                _emit(config, "agent:log", {"message": f"  Dropped invalid pin: {p} (net {name})"})
                continue
            if p in used_pins:
                _emit(config, "agent:log", {"message": f"  Dropped duplicate pin: {p} (net {name})"})
                continue
            used_pins.add(p)
            clean.append(p)
        if len(clean) >= 2 or (_is_gnd_net(name) or _is_power_net(name)) and len(clean) >= 1:
            valid_nets.append({"net": name, "pins": clean})
    for net in valid_nets:
        if _is_gnd_net(net["net"]):
            for p in net["pins"]:
                pname = pins[p].get("name", "").upper()
                if _is_power_net(pname):
                    net["pins"].remove(p)
                    used_pins.discard(p)
                    _emit(config, "agent:log", {"message": f"  ERC: removed power pin {p} ({pname}) from GND net"})
    power_pins = []
    netlist = []
    n_power_nets = 0
    n_signal_nets = 0
    for net in valid_nets:
        name = net["net"]
        if _is_gnd_net(name) or _is_power_net(name):
            n_power_nets += 1
            canonical = "GND" if _is_gnd_net(name) else name.upper().lstrip('+')
            for p in net["pins"]:
                power_pins.append({"pin": p, "net": canonical})
        else:
            n_signal_nets += 1
            ps = net["pins"]
            if len(ps) >= 2:
                hub = ps[0]
                for i in range(1, len(ps)):
                    netlist.append({"source": hub, "target": ps[i], "net": name})
    connected_refs = set()
    for conn in netlist:
        connected_refs.add(conn["source"].split(":")[0])
        connected_refs.add(conn["target"].split(":")[0])
    for pp in power_pins:
        connected_refs.add(pp["pin"].split(":")[0])
    all_refs = {c["ref_des"] for c in state.get("selected_components", [])}
    orphans = sorted(all_refs - connected_refs)
    if orphans:
        _emit(config, "agent:log", {
            "message": f"  WARNING: {len(orphans)} unconnected component(s): {', '.join(orphans)}. "
                       f"Attaching their power/ground pins to nets."
        })
        for ref in orphans:
            orphan_pins = [k for k in pins if k.split(":")[0] == ref and k not in used_pins]
            is_2pin_passive = len([k for k in pins if k.startswith(f"{ref}:")]) <= 2
            for key in orphan_pins:
                p_pin = pins[key]
                pname = p_pin.get("name", "").upper()
                if _is_gnd_net(pname):
                    power_pins.append({"pin": key, "net": "GND"})
                    used_pins.add(key)
                elif _is_power_net(pname):
                    power_pins.append({"pin": key, "net": pname.lstrip('+')})
                    used_pins.add(key)
                elif p_pin.get("etype") in POWER_ETYPES and pname and pname != "~":
                    power_pins.append({"pin": key, "net": pname.lstrip('+')})
                    used_pins.add(key)
                elif is_2pin_passive:
                    if len([k for k in power_pins if k["pin"].startswith(f"{ref}:")]) == 0:
                        power_pins.append({"pin": key, "net": "GND"})
                        used_pins.add(key)
                        _emit(config, "agent:log", {
                            "message": f"  Passive orphan rescue: {key} -> GND"
                        })
    refs_with_signals = set()
    for conn in netlist:
        refs_with_signals.add(conn["source"].split(":")[0])
        refs_with_signals.add(conn["target"].split(":")[0])
    hub_ref = max(
        (k.split(":")[0] for k in pins),
        key=lambda r: sum(1 for k in pins if k.startswith(f"{r}:")),
        default=None
    )
    if hub_ref:
        for comp in state.get("selected_components", []):
            ref = comp["ref_des"]
            if ref in refs_with_signals or ref == hub_ref:
                continue
            spare_hub = sorted(
                k for k in pins
                if k.startswith(f"{hub_ref}:") and k not in used_pins
                and pins[k].get("etype") in ("bidirectional", "input", "output", "passive")
            )
            ref_signal = sorted(
                k for k in pins
                if k.startswith(f"{ref}:") and k not in used_pins
            )
            if spare_hub and ref_signal:
                hub_pin = spare_hub[0]
                ref_pin = ref_signal[0]
                net_name = pins[ref_pin].get("name", "").upper() or f"{ref}_SIG"
                _merge_net(nets, net_name, [hub_pin, ref_pin])
                netlist.append({"source": hub_pin, "target": ref_pin, "net": net_name})
                used_pins.add(hub_pin)
                used_pins.add(ref_pin)
                refs_with_signals.add(ref)
                _emit(config, "agent:log", {
                    "message": f"  Auto-routed {ref_pin} ({pins[ref_pin]['name']}) -> {hub_pin} ({pins[hub_pin]['name']})"
                })
    _emit(config, "agent:log", {
        "message": f"Nets: {n_power_nets} power/GND ({len(power_pins)} pins as power symbols), "
                   f"{n_signal_nets} signal ({len(netlist)} wire connections)"
    })
    _emit_activity(config, "netlist", "Netlist Generation", "update", level="success", kind="netlist",
                   detail=f"Generated {len(netlist)} connections across {n_power_nets + n_signal_nets} nets")
    _emit_activity(config, "netlist", "Netlist Generation", "done")
    return {"netlist": netlist, "nets": valid_nets, "power_pins": power_pins}
