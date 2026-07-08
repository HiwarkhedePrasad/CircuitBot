import json
from functools import lru_cache

from agent.bus_checker import check_bus_topology
from agent.power_domains import POWER_NETS as _CANONICAL_POWER_NETS
from agent.prompts import NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _make_signal_batches, _merge_net, _generate_nets_fallback,
    _clean_json, _call_llm_with_tools,
    _is_gnd_net, _is_power_net, _canonical_signal_name, _resolve_hallucinated_pin,
    PIN_ALIASES, POWER_ETYPES, MAX_BATCH_PINS, _parse_sexpr_to_ops, _extract_pins_from_ops,
)


def _pin_summary_from_matrix(ref_des: str, pin_matrix: dict) -> str:
    """Generate a compact pin summary from pin_matrix data alone.

    Avoids RAG dependency — uses only data already in state.
    Returns e.g. ``"3 pins (1 output, 2 power_in)"`` or ``""``.
    """
    counts: dict[str, int] = {}
    for key, pin in pin_matrix.items():
        if key.split(":")[0] == ref_des:
            etype = pin.get("etype", "passive")
            counts[etype] = counts.get(etype, 0) + 1
    if not counts:
        return ""
    total = sum(counts.values())
    parts = [f"{n} {t}" for t, n in sorted(counts.items(), key=lambda x: -x[1])]
    return f"{total} pins ({', '.join(parts)})"


@lru_cache(maxsize=1)
def _load_knowledge_db() -> dict:
    """Load the pre-built knowledge database (cached after first call)."""
    import os
    db_path = os.path.join(
        os.path.dirname(__file__), "..", "..",
        "netlist-preprocessing-experiment", "knowledge_db.json"
    )
    if not os.path.isfile(db_path):
        return {}
    with open(db_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _format_comp_for_netlist(
    comp: dict,
    pin_matrix: dict,
    batch_refs_set: set[str],
) -> str:
    """Format a component entry for the netlist LLM prompt.

    Uses structured knowledge from the pre-built knowledge DB when available,
    falling back to in-band metadata only (pin_summary, no raw datasheet).
    """
    ref = comp["ref_des"]
    desc = comp.get("description", "")
    ps = _pin_summary_from_matrix(ref, pin_matrix)

    lines = [f'  {ref}: {comp["id_str"]} ({comp["category"]})']
    if desc:
        lines.append(f"    description: {desc}")
    if ps:
        lines.append(f"    pin_summary: {ps}")

    # Structured knowledge from pre-built DB — much smaller than raw datasheet text
    id_str = comp.get("id_str", "")
    if id_str:
        try:
            from agent.knowledge_extractor import format_knowledge_for_prompt
            db = _load_knowledge_db()
            entry = db.get(id_str)
            if entry:
                ktext = format_knowledge_for_prompt(entry)
                if ktext:
                    lines.append(f"    knowledge: {ktext}")
        except Exception:
            pass

    return "\n".join(lines)


def netlist_node(state, config):
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})
    emit_assistant_message(config, "Generating the netlist — connecting all selected components into a wiring topology...")
    emit_tool_event(config, "Netlist Generation", "running", "Connecting pins...")
    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": [], "nets": [], "power_pins": []}
    comps_desc = "\n".join(
        f'  {c["ref_des"]}: {c["id_str"]} ({c["category"]})'
        for c in comps
    )
    from agent.power_domains import classify as _classify_rail, is_gnd as _is_gnd, is_power as _is_power
    assigned = set()
    power_groups = {}
    for key, pin in pins.items():
        pname = pin.get("name", "").strip().upper()
        if _is_gnd(pname):
            power_groups.setdefault("GND", []).append(key)
            assigned.add(key)
        elif _is_power(pname):
            canon = _classify_rail(pname) or pname.lstrip("+")
            power_groups.setdefault(canon, []).append(key)
            assigned.add(key)
    nets = [{"net": n, "pins": p} for n, p in power_groups.items()]
    _emit(config, "agent:log", {
        "message": f"  Power/GND pre-assigned deterministically: {len(assigned)} pins -> "
                   f"{', '.join(power_groups) or 'none'}"
    })

    # ── Deterministic pin matcher ──────────────────────────────────────
    try:
        from agent.pin_matcher import match_pins
        match_result = match_pins(comps, pins, nets, assigned=assigned)
        if match_result["new_nets"]:
            for net_entry in match_result["new_nets"]:
                _merge_net(nets, net_entry["net"], net_entry["pins"])
        if match_result.get("matched_pins"):
            n_new = len(match_result["matched_pins"])
            assigned.update(match_result["matched_pins"])
            _emit(config, "agent:log", {
                "message": f"  Pin matcher: assigned {n_new} pin(s) deterministically"
            })
    except Exception:
        _emit(config, "agent:log", {"message": "  Pin matcher skipped (error)"})

    signal_keys = [k for k in pins if k not in assigned]
    batches = _make_signal_batches(signal_keys, max_pins=MAX_BATCH_PINS)
    if len(batches) > 1:
        _emit(config, "agent:log", {
            "message": f"  Wiring {len(signal_keys)} signal pins in {len(batches)} batches"
        })
    trace_constraints: dict = {}
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
            _format_comp_for_netlist(c, pins, batch_refs_set)
            for c in comps
            if c["ref_des"] in batch_refs_set
        )
        pins_desc = "\n".join(
            f'  {k}: pin_name="{pins[k]["name"]}"  etype="{pins[k]["etype"]}"'
            for k in batch_keys
        )
        existing = ", ".join(n["net"] for n in nets) or "(none yet)"
        text = _call_llm_with_tools(NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER.format(
            prompt=state["prompt"],
            components_desc=batch_comps_desc,
            existing_nets=existing,
            pins_desc=pins_desc,
        ))
        text = _clean_json(text)
        try:
            batch_data = json.loads(text) if text else []
        except json.JSONDecodeError:
            print(f"Batch {bi}: failed to parse nets JSON: {text[:200]}")
            batch_data = []
        batch_tc = {}
        if isinstance(batch_data, dict):
            batch_tc = batch_data.get("trace_constraints", {})
            batch_data = batch_data.get("nets", [])
        if batch_tc:
            trace_constraints.update(batch_tc)
        if not isinstance(batch_data, list):
            continue
        batch_nets = batch_data
        # Normalize list-of-lists format (e.g. [["GND", ["p1","p2"]]])
        # into dict format (e.g. [{"net": "GND", "pins": ["p1","p2"]}])
        normalized = []
        for entry in batch_nets:
            if isinstance(entry, dict):
                normalized.append(entry)
            elif isinstance(entry, list) and len(entry) == 2:
                name_part, pins_part = entry
                if isinstance(name_part, str) and isinstance(pins_part, list):
                    normalized.append({"net": name_part, "pins": pins_part})
        batch_nets = normalized
        n_dropped = 0
        n_resolved = 0
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
                if p in batch_key_set:
                    if p in assigned:
                        n_dropped += 1
                        continue
                    assigned.add(p)
                    clean.append(p)
                else:
                    resolved = _resolve_hallucinated_pin(p, pins, assigned)
                    if resolved and resolved not in assigned:
                        assigned.add(resolved)
                        clean.append(resolved)
                        n_resolved += 1
                    else:
                        n_dropped += 1
            if clean:
                _merge_net(nets, name, clean)
        total_unmatched = n_dropped + n_resolved
        if total_unmatched:
            _emit(config, "agent:log", {
                "message": f"  Batch {bi}: {n_resolved} fuzzy-resolved, {n_dropped} dropped (of {total_unmatched} unmatched)"
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
            keep = []
            for p in net["pins"]:
                pname = pins[p].get("name", "").upper()
                if _is_power_net(pname):
                    used_pins.discard(p)
                    _emit(config, "agent:log", {"message": f"  ERC: removed power pin {p} ({pname}) from GND net"})
                else:
                    keep.append(p)
            net["pins"] = keep
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
                for i in range(len(ps) - 1):
                    netlist.append({"source": ps[i], "target": ps[i+1], "net": name})
    n_power_nets = len(power_groups)
    _emit(config, "agent:log", {
        "message": f"Nets: {n_power_nets} power/GND ({len(power_pins)} pins as power symbols), "
                   f"{n_signal_nets} signal ({len(netlist)} wire connections)"
    })
    emit_tool_event(config, "Netlist Generation", "completed",
                    f"{len(netlist)} connections across {n_power_nets + n_signal_nets} nets")
    emit_assistant_message(config, f"Generated {len(netlist)} connections across {n_power_nets + n_signal_nets} nets.")
    if trace_constraints:
        _emit(config, "agent:log", {
            "message": f"  Trace constraints from LLM: {len(trace_constraints)} net(s) with custom widths/impedances"
        })
    # ── Inject PWR_FLAG for power nets without a power-output driver ──
    result = {"netlist": netlist, "nets": valid_nets, "power_pins": power_pins,
              "trace_constraints": trace_constraints}
    # ── Build canonical synthesis graph ──────────────────────────────
    try:
        from agent.synthesis.graph import SynthesisGraph
        from agent.synthesis.classifier import classify_all
        from agent.synthesis.topology import match_and_constrain
        sgraph = SynthesisGraph()
        for c in comps:
            sgraph.add_component(c)
        for pk, pd in pins.items():
            ref = pk.split(":")[0] if ":" in pk else ""
            sgraph.add_pin(ref, pk, pd)
        sgraph.import_llm_nets(netlist)
        sgraph.import_power_pins(power_pins)
        classify_all(sgraph)
        match_and_constrain(sgraph)
        result["synthesis_graph"] = {
            "components": {r: {"ref_des": r, "id_str": c.id_str, "library": c.library,
                               "category": c.category, "user_locked": c.user_locked,
                               "pins": {pk: {"name": p.name, "role": p.role.value, "etype": p.etype}
                                        for pk, p in c.pins.items()}}
                           for r, c in sgraph.components.items()},
            "nets": {n: {"name": n, "role": nr.role.value, "pins": sorted(nr.pins)}
                     for n, nr in sgraph.nets.items()},
            "constraints": [{"type": ct.type.value, "source_pin": ct.source_pin,
                              "target_pin": ct.target_pin, "metadata": ct.metadata}
                             for ct in sgraph.constraints],
        }
        _emit(config, "agent:log", {
            "message": (f"Synthesis graph: {len(sgraph.components)} components, "
                        f"{len(sgraph.nets)} nets, {len(sgraph.constraints)} constraints")
        })
    except Exception as exc:
        _emit(config, "agent:log", {"message": f"Synthesis graph build failed: {exc}"})

    try:
        import copy
        from agent.tools import fetch_sexpr as _fetch_sexpr
        from agent.utils import _create_pwr_flag_component

        need_flag = []
        for net in valid_nets:
            name = net["net"].upper().lstrip("+")
            if name in _CANONICAL_POWER_NETS or _is_power_net(name) or _is_gnd_net(name):
                has_po = any(
                    pins[p].get("etype", "").lower() == "power_out"
                    for p in net["pins"] if p in pins
                )
                if not has_po:
                    need_flag.append(name)
        if need_flag:
            sexpr = _fetch_sexpr("power:PWR_FLAG")
            flag_ops = _parse_sexpr_to_ops(sexpr, "power")
            flag_pin_raw = _extract_pins_from_ops(flag_ops, "_PWRF")
            selected = state.get("selected_components", [])
            comp_ops = state.get("component_ops", {})
            pm = state.get("pin_matrix", {})
            for i, net_name in enumerate(sorted(need_flag)):
                fc = _create_pwr_flag_component(
                    net_name, i + 1, flag_ops, flag_pin_raw,
                    f"PWR_FLAG: net {net_name} has no power-output pin",
                )
                selected.append(fc["component"])
                comp_ops[fc["ref"]] = copy.deepcopy(fc["comp_op"])
                pkey, pval = fc["pin_entry"]
                pm[pkey] = pval
                power_pins.append(fc["power_pin_entry"])
            result["selected_components"] = selected
            result["component_ops"] = comp_ops
            result["pin_matrix"] = pm
            _emit(config, "agent:log", {
                "message": (f"  Injected PWR_FLAG on {len(need_flag)} net(s): "
                           f"{', '.join(sorted(need_flag))}")
            })
    except Exception as e:
        print(f"PWR_FLAG injection failed: {e}")
    return result
