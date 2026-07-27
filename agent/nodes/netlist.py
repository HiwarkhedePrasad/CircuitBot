import json
import logging
from functools import lru_cache

from agent.bus_checker import check_bus_topology
from agent.power_domains import POWER_NETS as _CANONICAL_POWER_NETS
from agent.prompts import NETLIST_BATCH_SYSTEM, NETLIST_BATCH_USER
from agent.feature_flags import is_enabled
from agent.utils import (
    _emit, emit_assistant_message, emit_tool_event, _make_signal_batches, _merge_net, _generate_nets_fallback,
    _clean_json, _call_llm_with_tools,
    _is_gnd_net, _is_power_net, _canonical_signal_name, _resolve_hallucinated_pin,
    PIN_ALIASES, POWER_ETYPES, MAX_BATCH_PINS, _parse_sexpr_to_ops, _extract_pins_from_ops,
)

logger = logging.getLogger(__name__)


def _pin_anchor_priority(pin_key: str, pin_matrix: dict, passive_refs: set[str]) -> tuple[int, str]:
    ref = pin_key.split(":")[0] if ":" in pin_key else pin_key
    etype = str(pin_matrix.get(pin_key, {}).get("etype", "") or "").lower()
    if etype in ("output", "open_collector", "open_emitter"):
        rank = 0
    elif etype == "bidirectional":
        rank = 1
    elif etype in ("input", "tri_state"):
        rank = 2
    elif etype == "passive":
        rank = 3
    else:
        rank = 4
    if ref in passive_refs:
        rank += 10
    return (rank, pin_key)


def _signal_edges_for_net(net_name: str, net_pins: list[str], pin_matrix: dict, passive_refs: set[str]) -> list[dict]:
    unique_pins: list[str] = []
    seen: set[str] = set()
    for pin_key in net_pins:
        if pin_key in seen:
            continue
        seen.add(pin_key)
        unique_pins.append(pin_key)
    if len(unique_pins) < 2:
        return []
    if len(unique_pins) == 2:
        return [{"source": unique_pins[0], "target": unique_pins[1], "net": net_name}]

    anchor = min(unique_pins, key=lambda p: _pin_anchor_priority(p, pin_matrix, passive_refs))
    others = sorted((p for p in unique_pins if p != anchor), key=lambda p: _pin_anchor_priority(p, pin_matrix, passive_refs))
    return [{"source": anchor, "target": pin_key, "net": net_name} for pin_key in others]


def _flag_net_name(component: dict) -> str:
    for field in ("description", "justification"):
        text = str(component.get(field, "") or "")
        if "Power flag for " in text:
            return text.split("Power flag for ", 1)[1].split()[0].upper().lstrip("+")
        if "PWR_FLAG: net " in text:
            return text.split("PWR_FLAG: net ", 1)[1].split()[0].upper().lstrip("+")
    return ""


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

    comp_pins = []
    for key, pinfo in pin_matrix.items():
        if key.split(":")[0] == ref:
            pnum = pinfo.get("num") or pinfo.get("number") or (key.split(":")[-1] if ":" in key else "")
            pname = pinfo.get("name", "")
            petype = pinfo.get("etype", "passive")
            comp_pins.append(f"{pnum}:{pname}({petype})")
    if comp_pins:
        lines.append(f"    pins: [{', '.join(comp_pins[:20])}]")

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


def _build_research_context(state: dict) -> str:
    """Build research context from web research, datasheets, and connection guidance."""
    research_parts = []

    web_research = state.get("web_research_results", [])
    if web_research:
        lines = []
        for r in web_research:
            sub = r.get("subsystem", "")
            summary = r.get("summary", "")
            if summary and not summary.startswith("("):
                lines.append(f"[{sub}] {summary[:400]}")
        if lines:
            research_parts.append("Web component research:\n" + "\n".join(lines))

    ds_results = state.get("datasheet_search_results", [])
    if ds_results:
        lines = []
        for r in ds_results:
            ref = r.get("ref_des", "")
            id_str = r.get("id_str", "")
            summary = r.get("summary", "")
            if summary and not summary.startswith("("):
                lines.append(f"[{ref} ({id_str})] {summary[:400]}")
        if lines:
            research_parts.append("Datasheet information:\n" + "\n".join(lines))

    conn_results = state.get("connection_search_results", [])
    if conn_results:
        lines = []
        for r in conn_results:
            title = r.get("title", "")
            summary = r.get("summary", "")
            if summary and not summary.startswith("("):
                lines.append(f"[{title}] {summary[:300]}")
        if lines:
            research_parts.append("Connection wiring guidance (from web research):\n" + "\n".join(lines))

    research_context = "\n\n".join(research_parts)
    if research_context:
        research_context += "\n\nUse the above research to guide your wiring decisions."
    return research_context


def _preassign_power_nets(pins: dict, comps: list[dict]) -> tuple[list[dict], set[str], dict[str, list[str]]]:
    """Pre-assign power/GND pins deterministically. Returns (nets, assigned, power_groups)."""
    from agent.power_domains import classify as _classify_rail, is_gnd as _is_gnd, is_power as _is_power

    has_usb_c_input = any(
        "USB_C" in (comp.get("id_str", "") or "").upper()
        or "USB-C" in (comp.get("id_str", "") or "").upper()
        for comp in comps
    )
    has_3v3_regulator = any(
        (comp.get("id_str", "") or "").startswith("Regulator_")
        and any(token in (comp.get("id_str", "") or "").upper() for token in ("3.3", "_33", "-33"))
        for comp in comps
    )

    existing_flag_nets_by_ref = {
        c.get("ref_des", ""): _flag_net_name(c)
        for c in comps
        if c.get("id_str") == "power:PWR_FLAG"
    }
    assigned = set()
    power_groups: dict[str, list[str]] = {}
    for key, pin in pins.items():
        ref = key.split(":")[0] if ":" in key else key
        pname = pin.get("name", "").strip().upper()
        if _is_gnd(pname):
            power_groups.setdefault("GND", []).append(key)
            assigned.add(key)
        elif _is_power(pname):
            canon = _classify_rail(pname) or pname.lstrip("+")
            # These are explicit topology rules, not a generic rail merge.
            # A USB-C sink feeds the regulator input from VBUS; a known 3.3V
            # regulator feeds generic VDD consumers on its regulated rail.
            if pname == "VIN" and has_usb_c_input:
                canon = "VBUS"
            elif pname == "VDD" and has_3v3_regulator:
                canon = "3V3"
            power_groups.setdefault(canon, []).append(key)
            assigned.add(key)
        elif ref in existing_flag_nets_by_ref:
            power_groups.setdefault(existing_flag_nets_by_ref[ref], []).append(key)
            assigned.add(key)
    nets = [{"net": n, "pins": p} for n, p in power_groups.items()]
    return nets, assigned, power_groups


def _run_deterministic_pin_matcher(
    config, comps, pins, nets, assigned
) -> None:
    """Run the deterministic pin matcher and merge results into nets/assigned."""
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


def _process_signal_batches(
    config, state, comps, pins, nets, assigned, batches, signal_keys,
    research_context, passive_refs
) -> tuple[dict, list[dict]]:
    """Process signal pins in batches via LLM. Returns (trace_constraints, explicit_signal_edges)."""
    trace_constraints: dict = {}
    explicit_signal_edges: list[dict] = []

    try:
        from config import ensure_proxy
        ensure_proxy(timeout=15)
    except Exception:
        pass

    for bi, batch_refs in enumerate(batches, 1):
        batch_tc = {}
        batch_keys = sorted(
            k for k in signal_keys
            if k.split(":")[0] in batch_refs and k not in assigned
        )
        if not batch_keys:
            continue
        _emit(config, "agent:thinking", {
            "message": "Planning all pin connections..."
        })
        batch_refs_set = set(batch_refs)
        batch_comps_desc = "\n".join(
            _format_comp_for_netlist(c, pins, batch_refs_set)
            for c in comps
            if c.get("ref_des") in batch_refs_set
        )
        pins_desc = "\n".join(
            f'  {k}: pin_name="{pins[k]["name"]}"  etype="{pins[k]["etype"]}"'
            for k in batch_keys
        )
        existing = ", ".join(n["net"] for n in nets) or "(none yet)"
        user_prompt = NETLIST_BATCH_USER.format(
            prompt=state["prompt"],
            components_desc=batch_comps_desc,
            research_context=research_context,
            existing_nets=existing,
            pins_desc=pins_desc,
        )
        text = _call_llm_with_tools(
            NETLIST_BATCH_SYSTEM, user_prompt,
            max_tool_rounds=4,
        )
        text = _clean_json(text)
        try:
            batch_data = json.loads(text) if text else []
        except json.JSONDecodeError:
            logger.warning(f"Batch {bi}: failed to parse nets JSON: {text[:200]}")
            batch_data = []
        if isinstance(batch_data, dict):
            batch_tc = batch_data.get("trace_constraints", {})
            batch_data = batch_data.get("nets", [])
        if batch_tc:
            trace_constraints.update(batch_tc)
        if not isinstance(batch_data, list):
            continue
        batch_nets = batch_data
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
                explicit_signal_edges.extend(_signal_edges_for_net(name, clean, pins, passive_refs))
        total_unmatched = n_dropped + n_resolved
        if total_unmatched:
            _emit(config, "agent:log", {
                "message": f"  Batch {bi}: {n_resolved} fuzzy-resolved, {n_dropped} dropped (of {total_unmatched} unmatched)"
            })

    return trace_constraints, explicit_signal_edges


def _validate_and_clean_nets(config, nets, pins, assigned) -> list[dict]:
    """Validate nets, remove duplicates/invalids, fix ERC issues. Returns valid_nets."""
    leftover = {k: pins[k] for k in pins if k not in assigned}
    if leftover:
        for net in _generate_nets_fallback(leftover, {}, nets):
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
        else:
            for p in clean:
                assigned.discard(p)
                used_pins.discard(p)

    erc_removed_pins: list[tuple[str, str]] = []
    for net in valid_nets:
        if _is_gnd_net(net["net"]):
            keep = []
            for p in net["pins"]:
                pname = pins[p].get("name", "").upper()
                if _is_power_net(pname):
                    used_pins.discard(p)
                    erc_removed_pins.append((p, pname))
                    _emit(config, "agent:log", {"message": f"  ERC: removed power pin {p} ({pname}) from GND net"})
                else:
                    keep.append(p)
            net["pins"] = keep

    if erc_removed_pins:
        from agent.power_domains import classify as _classify_rail
        net_by_canon: dict[str, list[str]] = {}
        for vn in valid_nets:
            if not _is_gnd_net(vn["net"]):
                net_by_canon.setdefault(vn["net"].upper(), []).extend(vn["pins"])
        for p, pname in erc_removed_pins:
            canon = _classify_rail(pname) or pname.lstrip("+")
            if canon.upper() in net_by_canon:
                net_by_canon[canon.upper()].append(p)
                used_pins.add(p)
                for vn in valid_nets:
                    if vn["net"].upper() == canon.upper():
                        assert isinstance(vn["pins"], list)
                        vn["pins"].append(p)
                        break
                _emit(config, "agent:log", {"message": f"  ERC: re-homed {p} ({pname}) to net '{canon}'"})
            else:
                assigned.discard(p)
                _emit(config, "agent:log", {"message": f"  ERC: orphaned {p} ({pname}) — no power net '{canon}' found"})

    return valid_nets


def _build_synthesis_graph(config, comps, pins, netlist, power_pins) -> tuple:
    """Build SynthesisGraph and run deterministic validation. Returns (graph_or_None, validation_issues)."""
    validation_issues = []
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

        _emit(config, "agent:log", {
            "message": (f"Synthesis graph: {len(sgraph.components)} components, "
                        f"{len(sgraph.nets)} nets, {len(sgraph.constraints)} constraints")
        })

        if is_enabled("VALIDATION_DETERMINISTIC"):
            try:
                from agent.synthesis.validation import validate_circuit
                from agent.synthesis.engine import validate_constraints, suggest_repairs

                validation_issues = list(validate_circuit(sgraph))
                constraint_viols = validate_constraints(sgraph)
                repairs = suggest_repairs(constraint_viols)

                for cv in constraint_viols:
                    validation_issues.append({
                        "code": "CON001",
                        "severity": cv.severity,
                        "stage": "synthesis",
                        "message": cv.description,
                    })

                if validation_issues:
                    for issue in validation_issues:
                        severity = issue.get("severity", "warning")
                        code = issue.get("code", "UNKNOWN")
                        msg = issue.get("message", "")
                        emit_thought(config, f"Validation [{severity}] {code}: {msg}")

                if repairs:
                    emit_thought(config, f"Suggested {len(repairs)} repair(s) for constraint violations")
                    for r in repairs:
                        emit_thought(config, f"  Repair: {r.description} (priority {r.priority})")

                _emit(config, "agent:log", {
                    "message": f"Deterministic validation: {len(validation_issues)} issues, {len(repairs)} repairs suggested"
                })
            except Exception as ve:
                _emit(config, "agent:log", {"message": f"  Deterministic validation failed: {ve}"})

        return sgraph, validation_issues
    except Exception as exc:
        _emit(config, "agent:log", {"message": f"Synthesis graph build failed: {exc}"})
        return None, validation_issues


def _inject_pwr_flags(config, state, valid_nets, pins, power_pins) -> dict:
    """Inject PWR_FLAG components for power nets without a power-output driver.
    Returns dict of state overrides to merge into result."""
    overrides = {}
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
            selected = list(state.get("selected_components", []))
            comp_ops = dict(state.get("component_ops", {}))
            pm = dict(state.get("pin_matrix", {}))
            existing_flag_nets = {
                _flag_net_name(comp) for comp in selected
                if comp.get("id_str") == "power:PWR_FLAG"
            }
            existing_flag_nets.discard("")
            existing_flag_indices = []
            for comp in selected:
                ref = str(comp.get("ref_des", "") or "")
                if ref.startswith("#FLG") and ref[4:].isdigit():
                    existing_flag_indices.append(int(ref[4:]))
            next_index = max(existing_flag_indices, default=0)
            injected_flag_nets: list[str] = []
            for net_name in sorted(need_flag):
                if net_name in existing_flag_nets:
                    continue
                next_index += 1
                fc = _create_pwr_flag_component(
                    net_name, next_index, flag_ops, flag_pin_raw,
                    f"PWR_FLAG: net {net_name} has no power-output pin",
                )
                selected.append(fc["component"])
                comp_ops[fc["ref"]] = copy.deepcopy(fc["comp_op"])
                pkey, pval = fc["pin_entry"]
                pm[pkey] = pval
                power_pins.append(fc["power_pin_entry"])
                injected_flag_nets.append(net_name)
            overrides["selected_components"] = selected
            overrides["component_ops"] = comp_ops
            overrides["pin_matrix"] = pm
            if injected_flag_nets:
                _emit(config, "agent:log", {
                    "message": (f"  Injected PWR_FLAG on {len(injected_flag_nets)} net(s): "
                               f"{', '.join(injected_flag_nets)}")
                })
    except Exception as e:
        logger.error(f"PWR_FLAG injection failed: {e}")
    return overrides


def netlist_node(state, config):
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})
    emit_assistant_message(config, "Generating the netlist — connecting all selected components into a wiring topology...")
    emit_tool_event(config, "Netlist Generation", "running", "Connecting pins...")
    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": [], "nets": [], "power_pins": []}

    # ── 1. Build research context ──
    research_context = _build_research_context(state)
    if research_context:
        _emit(config, "agent:log", {
            "message": f"  Injecting research context into netlist prompt"
        })

    # ── 2. Pre-assign power/GND pins ──
    nets, assigned, power_groups = _preassign_power_nets(pins, comps)
    _emit(config, "agent:log", {
        "message": f"  Power/GND pre-assigned deterministically: {len(assigned)} pins -> "
                   f"{', '.join(power_groups) or 'none'}"
    })

    # ── 3. Deterministic pin matcher ──
    _run_deterministic_pin_matcher(config, comps, pins, nets, assigned)

    # ── 4. Process signal pins in batches via LLM ──
    signal_keys = [k for k in pins if k not in assigned]
    batches = _make_signal_batches(signal_keys)
    if signal_keys:
        _emit(config, "agent:log", {
            "message": f"  Wiring {len(signal_keys)} signal pins in {len(batches)} batch(es)"
        })
    passive_refs = {
        c.get("ref_des", "") for c in comps
        if str(c.get("category", "") or "").upper() in ("DEVICE", "RESISTOR", "CAPACITOR", "INDUCTOR", "DIODE")
        or str(c.get("id_str", "") or "").startswith("Device:")
    }
    trace_constraints, explicit_signal_edges = _process_signal_batches(
        config, state, comps, pins, nets, assigned, batches, signal_keys,
        research_context, passive_refs
    )

    # ── 5. Bus topology check ──
    nets, bus_warnings, bus_removed_pins = check_bus_topology(nets, pins, comps)
    if bus_removed_pins:
        old_count = len(assigned)
        for p in bus_removed_pins:
            assigned.discard(p)
        _emit(config, "agent:log", {
            "message": f"  Bus check: freed {old_count - len(assigned)} pin(s) from assigned "
                       f"for fallback recovery"
        })
    for w in bus_warnings:
        _emit(config, "agent:log", {"message": w})
    _bus_warnings_for_result = list(bus_warnings) if is_enabled("BUS_WARNINGS_SURFACED") else []

    # ── 6. Preserve power domains ──
    # Power rails are canonicalized by _preassign_power_nets. Never infer a
    # connection from electrical type alone: VBUS, VIN, 5V, and 3V3 can each
    # legitimately contain a source and must only meet through an explicit
    # regulator or power-path component.

    # ── 7. Validate and clean nets ──
    valid_nets = _validate_and_clean_nets(config, nets, pins, assigned)

    # ── 8. Auto pull-up detection ──
    netlist_validation_issues: list[dict] = []
    try:
        from agent.auto_pullup import find_missing_pullups
        pullup_issues = find_missing_pullups(valid_nets, pins, comps)
        for pi in pullup_issues:
            _emit(config, "agent:log", {"message": f"  {pi['code']}: {pi['message']}"})
            netlist_validation_issues.append(pi)
    except Exception as pu_ex:
        _emit(config, "agent:log", {"message": f"  Pull-up check failed: {pu_ex}"})

    # ── 9. Build netlist edges ──
    power_pins = []
    netlist = []
    seen_edges: set[tuple[str, str, str]] = set()

    def _append_edge(edge: dict):
        src = edge.get("source", "")
        tgt = edge.get("target", "")
        net_name = edge.get("net", "")
        if not src or not tgt or not net_name or src == tgt:
            return
        edge_key = (net_name.upper(),) + tuple(sorted((src, tgt)))
        if edge_key in seen_edges:
            return
        seen_edges.add(edge_key)
        netlist.append({"source": src, "target": tgt, "net": net_name})

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
            for edge in _signal_edges_for_net(name, net["pins"], pins, passive_refs):
                _append_edge(edge)
    for edge in explicit_signal_edges:
        _append_edge(edge)
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

    # ── 10. Build result ──
    result = {"netlist": netlist, "nets": valid_nets, "power_pins": power_pins,
              "trace_constraints": trace_constraints,
              "_validation_issues": netlist_validation_issues}
    if _bus_warnings_for_result:
        result["validation_warnings"] = _bus_warnings_for_result
        for w in _bus_warnings_for_result:
            emit_thought(config, f"Bus topology: {w}")

    # ── 11. Build SynthesisGraph ──
    sgraph, synth_issues = _build_synthesis_graph(config, comps, pins, netlist, power_pins)
    if sgraph:
        result["synthesis_graph"] = sgraph
    if synth_issues:
        result["_validation_issues"] = synth_issues

    # ── 12. Inject PWR_FLAG ──
    flag_overrides = _inject_pwr_flags(config, state, valid_nets, pins, power_pins)
    result.update(flag_overrides)

    return result
