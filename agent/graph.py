import json
import re
import os
import traceback
from langgraph.graph import StateGraph
from agent.state import AgentState
from agent.prompts import ANALYZE_SYSTEM, ANALYZE_USER, SELECT_SYSTEM, SELECT_USER, NETLIST_SYSTEM, NETLIST_USER
from agent.tools import search_components, fetch_sexpr, llm_call
from agent.layout_engine import BackendLayoutEngine


def _emit(config, event, data):
    emit_fn = config["configurable"].get("emit")
    if emit_fn:
        emit_fn(event, data)


def _clean_json(text: str) -> str:
    text = text.strip()
    start = text.find('[')
    if start < 0:
        start = text.find('{')
    if start < 0:
        return ''
    text = text[start:]
    if text.startswith('['):
        end = text.rfind(']')
        if end >= 0:
            text = text[:end+1]
    elif text.startswith('{'):
        end = text.rfind('}')
        if end >= 0:
            text = text[:end+1]
    text = re.sub(r'```json\s*|```\s*', '', text).strip()
    return text


def _call_llm(system: str, user: str) -> str:
    try:
        return llm_call(system, user)
    except Exception as e:
        print(f"LLM call failed: {e}")
        traceback.print_exc()
        return ""


# ── net classification helpers ───────────────────────────────────────────────

GND_NET_NAMES = {"GND", "GROUND", "VSS", "AGND", "DGND", "PGND", "GNDA", "GNDD", "EP", "EPAD", "0V"}
POWER_NET_NAMES = {"VCC", "VDD", "VBAT", "VIN", "VBUS", "V+", "V-", "VSYS", "VOUT", "VEE", "PWR"}


def _is_gnd_net(name: str) -> bool:
    return name.upper().lstrip('+') in GND_NET_NAMES


def _is_power_net(name: str) -> bool:
    n = name.upper().lstrip('+')
    if n in POWER_NET_NAMES:
        return True
    # Voltage-style names: 3V3, 5V, 12V, 1V8, V5 ...
    if re.match(r'^\d+V\d*$', n) or re.match(r'^V\d+$', n):
        return True
    return False


def _is_passive(id_str: str, category: str) -> bool:
    """Passives (R, C, L, crystals, LEDs) may legitimately appear multiple times."""
    cat = (category or '').upper()
    return id_str.startswith('Device:') or cat in ('DEVICE',)


def _ref_prefix_for(id_str: str, category: str) -> str:
    """Determine the correct reference designator prefix for a component."""
    name = id_str.partition(':')[2].upper()
    cat = (category or '').upper()
    if id_str.startswith('Device:'):
        if name == 'R' or name.startswith('R_'):
            return 'R'
        if name == 'C' or name.startswith(('C_', 'CP')):
            return 'C'
        if name == 'L' or name.startswith('L_'):
            return 'L'
        if name.startswith(('CRYSTAL', 'RESONATOR')):
            return 'Y'
        if name.startswith(('LED', 'D_')) or name == 'D':
            return 'D'
        if name.startswith('Q_'):
            return 'Q'
    if 'CONNECTOR' in cat:
        return 'J'
    if 'SWITCH' in cat:
        return 'SW'
    if 'DIODE' in cat or 'LED' in cat:
        return 'D'
    if 'CRYSTAL' in cat or 'OSCILLATOR' in cat:
        return 'Y'
    return 'U'


def analyze_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Analyzing your design request..."})
    text = _call_llm(ANALYZE_SYSTEM, ANALYZE_USER.format(prompt=state["prompt"]))
    text = _clean_json(text)
    try:
        analysis = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse analysis JSON: {text[:200]}")
        analysis = []
    if not analysis:
        analysis = [{"subsystem": state["prompt"], "function": "Main function", "example_components": []}]
    _emit(config, "agent:log", {
        "message": f"Identified {len(analysis)} subsystems: " +
                   ", ".join(a.get("subsystem", "?") for a in analysis)
    })
    return {"analysis": analysis}


def research_node(state: AgentState, config) -> dict:
    analysis = state.get("analysis", [])
    if not analysis:
        _emit(config, "agent:log", {"message": "No subsystems to research."})
        return {"research_results": []}

    all_results = []
    for sub in analysis:
        name = sub.get("subsystem", sub if isinstance(sub, str) else "unknown")
        examples = sub.get("example_components", [])
        if isinstance(examples, str):
            examples = [examples]
        queries = [name] + (examples[:2] if isinstance(examples, list) else [])
        _emit(config, "agent:thinking", {"message": f"Searching components for {name}..."})

        results = []
        for q in queries:
            try:
                results.extend(search_components(q, k=4))
            except Exception as e:
                print(f"Search failed for '{q}': {e}")

        seen = set()
        deduped = []
        for r in results:
            if r["id_str"] not in seen:
                seen.add(r["id_str"])
                deduped.append(r)

        all_results.append({
            "subsystem": name,
            "function": sub.get("function", ""),
            "results": deduped[:4],
        })
        _emit(config, "agent:log", {
            "message": f"  {name}: found {len(deduped)} candidates"
        })

    return {"research_results": all_results}


def select_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Selecting best components..."})

    research = state.get("research_results", [])
    if not research:
        _emit(config, "agent:log", {"message": "No research results to select from."})
        return {"selected_components": []}

    results_json = json.dumps(research, indent=2)
    text = _call_llm(SELECT_SYSTEM, SELECT_USER.format(
        prompt=state["prompt"], results_json=results_json
    ))
    text = _clean_json(text)
    try:
        selected = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse selection JSON: {text[:200]}")
        selected = []

    if not selected:
        ref_letters = "URCL"
        selected = []
        for i, sub in enumerate(research):
            results = sub.get("results", [])
            if results:
                best = results[0]
                ref = f"{ref_letters[i % len(ref_letters)]}{i + 1}"
                selected.append({
                    "id_str": best["id_str"],
                    "ref_des": ref,
                    "category": best["id_str"].split(":")[0] if ":" in best["id_str"] else "General",
                    "description": best.get("text", ""),
                })
    else:
        valid_ids = set()
        for sub in research:
            for r in sub.get("results", []):
                valid_ids.add(r["id_str"])
        filtered = []
        for s in selected:
            if s["id_str"] in valid_ids:
                filtered.append(s)
            else:
                _emit(config, "agent:log", {"message": f"  Rejected hallucinated ID: {s['id_str']}"})
                for sub in research:
                    results = sub.get("results", [])
                    if results:
                        filtered.append({
                            "id_str": results[0]["id_str"],
                            "ref_des": s["ref_des"],
                            "category": results[0]["id_str"].split(":")[0] if ":" in results[0]["id_str"] else "General",
                            "description": results[0].get("text", ""),
                        })
                        break
        selected = filtered

    # Deduplicate: complex ICs must be unique; passives (R, C, L, Y) may repeat.
    # Also normalize reference designator prefixes (no more "C3" microcontrollers).
    seen_ids = set()
    seen_refs = set()
    deduped = []
    ref_counter = {}

    def _next_ref(prefix):
        n = ref_counter.get(prefix, 0) + 1
        while f"{prefix}{n}" in seen_refs:
            n += 1
        ref_counter[prefix] = n
        return f"{prefix}{n}"

    for s in selected:
        id_str = s["id_str"]
        category = s.get("category", "")
        passive = _is_passive(id_str, category)

        if not passive and id_str in seen_ids:
            _emit(config, "agent:log", {"message": f"  Skipped duplicate IC: {id_str}"})
            continue

        # Enforce correct ref prefix for the component type
        correct_prefix = _ref_prefix_for(id_str, category)
        current_prefix = ''.join(c for c in s.get("ref_des", "") if c.isalpha()) or 'U'
        if current_prefix != correct_prefix or s.get("ref_des", "") in seen_refs:
            old_ref = s.get("ref_des", "?")
            s["ref_des"] = _next_ref(correct_prefix)
            if old_ref != s["ref_des"]:
                _emit(config, "agent:log", {"message": f"  Renamed {old_ref} -> {s['ref_des']}"})
        else:
            num_part = ''.join(c for c in s["ref_des"] if c.isdigit())
            if num_part:
                ref_counter[correct_prefix] = max(ref_counter.get(correct_prefix, 0), int(num_part))

        seen_ids.add(id_str)
        seen_refs.add(s["ref_des"])
        deduped.append(s)

    selected = deduped

    _emit(config, "agent:log", {
        "message": f"Selected {len(selected)} components: " +
                   ", ".join(f'{s["ref_des"]}={s["id_str"].split(":")[-1][:20]}' for s in selected)
    })
    return {"selected_components": selected}


def dispatch_node(state: AgentState, config) -> dict:
    pin_matrix = {}
    component_ops = {}

    for comp in state.get("selected_components", []):
        id_str = comp["id_str"]
        ref_des = comp["ref_des"]

        _emit(config, "agent:thinking", {"message": f"Loading {ref_des} ({id_str})..."})

        ops = []
        try:
            sexpr = fetch_sexpr(id_str)
            ops = _parse_sexpr_to_ops(sexpr, comp["category"])
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  Failed to load {ref_des}: trying search fallback..."})
            try:
                results = search_components(comp.get("description", ref_des), k=5)
                for r in results:
                    try:
                        sexpr = fetch_sexpr(r["id_str"])
                        ops = _parse_sexpr_to_ops(sexpr, r["id_str"].split(":")[0])
                        if ops:
                            id_str = r["id_str"]
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        if not ops:
            _emit(config, "agent:log", {"message": f"  Skipped {ref_des}: no symbol found"})
            continue

        _emit(config, "agent:component", {
            "id_str": id_str,
            "category": comp["category"],
            "ref_des": ref_des,
            "description": comp.get("description", ""),
            "ops": ops,
        })

        pins = _extract_pins_from_ops(ops, ref_des)
        
        # Log pin details for debugging
        pin_names = [f"{p['pin_num']}:{p['name']}" for p in pins.values()]
        _emit(config, "agent:log", {
            "message": f"  Added {ref_des} ({len(pins)} pins): {', '.join(pin_names[:5])}" + 
                      (f"... +{len(pins)-5} more" if len(pins) > 5 else "")
        })
        
        pin_matrix.update(pins)
        component_ops[ref_des] = ops

    _emit(config, "agent:log", {"message": "All components loaded. Planning layout and routing..."})

    return {"pin_matrix": pin_matrix, "component_ops": component_ops}


def netlist_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})

    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": [], "nets": [], "power_pins": []}

    comps_desc = "\n".join(
        f'  {c["ref_des"]}: {c["id_str"]} ({c["category"]})'
        for c in comps
    )
    pins_desc = "\n".join(
        f'  {k}: pin_name="{v["name"]}"'
        for k, v in sorted(pins.items())
    )

    text = _call_llm(NETLIST_SYSTEM, NETLIST_USER.format(
        prompt=state["prompt"],
        components_desc=comps_desc,
        pins_desc=pins_desc,
    ))
    text = _clean_json(text)
    try:
        nets = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse nets JSON: {text[:200]}")
        nets = []

    if not nets or not isinstance(nets, list):
        nets = _generate_nets_fallback(pins)

    # ── Validate nets: pins must exist, each pin in at most one net ──
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

    # ── ERC check: power/GND nets must not contain signal-looking pins shorted to rails ──
    # (basic sanity — a GND net containing a pin named "3V3" etc. is a short)
    for net in valid_nets:
        if _is_gnd_net(net["net"]):
            for p in net["pins"]:
                pname = pins[p].get("name", "").upper()
                if _is_power_net(pname):
                    net["pins"].remove(p)
                    used_pins.discard(p)
                    _emit(config, "agent:log", {"message": f"  ERC: removed power pin {p} ({pname}) from GND net"})

    # ── Split: power/GND nets become labels (no wires), signal nets get routed ──
    power_pins = []   # [{pin, net}] -> rendered as power symbols / global labels
    netlist = []      # pairwise signal connections for the A* router
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
            # Chain consecutive pins: A-B, B-C (not a full mesh)
            ps = net["pins"]
            for i in range(len(ps) - 1):
                netlist.append({"source": ps[i], "target": ps[i + 1], "net": name})

    # ── Orphan detection: every component should touch at least one net ──
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
        # Rescue: pull any GND/power-named pin of an orphan into the proper net
        for ref in orphans:
            for key, pin in pins.items():
                if key.split(":")[0] != ref or key in used_pins:
                    continue
                pname = pin.get("name", "").upper()
                if _is_gnd_net(pname):
                    power_pins.append({"pin": key, "net": "GND"})
                    used_pins.add(key)
                elif _is_power_net(pname):
                    power_pins.append({"pin": key, "net": pname.lstrip('+')})
                    used_pins.add(key)

    _emit(config, "agent:log", {
        "message": f"Nets: {n_power_nets} power/GND ({len(power_pins)} pins as power symbols), "
                   f"{n_signal_nets} signal ({len(netlist)} wire connections)"
    })
    return {"netlist": netlist, "nets": valid_nets, "power_pins": power_pins}


def layout_route_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Computing layout and routing wires..."})

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])
    power_pins = state.get("power_pins", [])

    if not comps or not comp_ops:
        _emit(config, "agent:done", {"message": "No components to place."})
        return {}

    engine = BackendLayoutEngine()
    for comp in comps:
        ref_des = comp["ref_des"]
        ops = comp_ops.get(ref_des)
        if not ops:
            continue
        engine.add_component(ref_des, ops, comp["category"])

    if not engine.components:
        _emit(config, "agent:done", {"message": "No components could be placed."})
        return {}

    engine.execute_placement()
    engine.build_obstacle_matrix(pin_matrix=pin_matrix)
    traces = engine.route_traces(netlist, pin_matrix)
    placements = engine.get_placements()

    # ── Compute power symbol/label positions (absolute coords + direction) ──
    power_labels = []
    for pp in power_pins:
        pin = pin_matrix.get(pp["pin"])
        if not pin:
            continue
        ref = pp["pin"].split(":")[0]
        comp = engine._get_comp(ref)
        if not comp:
            continue
        ax = pin["x"] + comp["x"]
        ay = pin["y"] + comp["y"]
        # Outward direction (away from component center)
        ccx = comp["x"] + comp["bbox"]["x"] + comp["bbox"]["w"] / 2
        ccy = comp["y"] + comp["bbox"]["y"] + comp["bbox"]["h"] / 2
        dx = ax - ccx
        dy = ay - ccy
        if abs(dx) >= abs(dy):
            direction = "right" if dx >= 0 else "left"
        else:
            direction = "up" if dy >= 0 else "down"
        power_labels.append({
            "pin": pp["pin"],
            "net": pp["net"],
            "x": ax,
            "y": ay,
            "dir": direction,
        })

    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": traces,
        "power_labels": power_labels,
        "netlist": netlist,
        "power_pins": power_pins,
    })

    _emit(config, "agent:done", {
        "message": f"Design complete: {len(engine.components)} components, "
                   f"{len(traces)} signal wires, {len(power_labels)} power symbols"
    })

    return {
        "component_placements": placements,
        "wire_paths": traces,
        "power_labels": power_labels,
    }


def _generate_nets_fallback(pin_matrix: dict) -> list:
    """Rule-based fallback: group pins into named nets by pin name."""
    by_name = {}
    for key, pin in pin_matrix.items():
        name = pin.get("name", "").strip().upper()
        if not name or name in ("~", "NC"):
            continue
        by_name.setdefault(name, []).append(key)

    nets = []

    # GND net: collect every ground-named pin
    gnd_pins = []
    for name in list(by_name.keys()):
        if _is_gnd_net(name):
            gnd_pins.extend(by_name.pop(name))
    if gnd_pins:
        nets.append({"net": "GND", "pins": gnd_pins})

    # Power nets grouped by canonical voltage name
    power_groups = {}
    for name in list(by_name.keys()):
        if _is_power_net(name):
            canon = name.lstrip('+')
            if canon in ("VCC", "VDD"):
                canon = "3V3"
            power_groups.setdefault(canon, []).extend(by_name.pop(name))
    for canon, pins_list in power_groups.items():
        nets.append({"net": canon, "pins": pins_list})

    # Signal nets: only when exactly 2 pins share a name (avoid GPIO meshes)
    for name, keys in by_name.items():
        if len(keys) == 2:
            nets.append({"net": name, "pins": keys})

    return nets


def _parse_sexpr_to_ops(sexpr_str: str, category: str) -> list:
    acc = []

    def parse(s):
        tokens, i = [], 0
        while i < len(s):
            c = s[i]
            if c == '(':
                tokens.append(c); i += 1
            elif c == ')':
                tokens.append(c); i += 1
            elif c in ' \t\n\r':
                i += 1
            elif c == '"':
                j = i + 1
                while j < len(s) and not (s[j] == '"' and s[j-1] != '\\'):
                    j += 1
                tokens.append(s[i:j+1]); i = j + 1
            else:
                j = i
                while j < len(s) and s[j] not in '() \t\n\r':
                    j += 1
                tokens.append(s[i:j]); i = j

        stack, root = [], []
        stack.append(root)
        for t in tokens:
            if t == '(':
                n = []; stack[-1].append(n); stack.append(n)
            elif t == ')':
                if len(stack) > 1: stack.pop()
            else:
                v = t[1:-1] if t.startswith('"') and t.endswith('"') else t
                stack[-1].append(v)
        return root[0] if root else []

    ast = parse(sexpr_str)
    if not ast:
        return acc

    def walk(node):
        if not isinstance(node, list):
            return
        typ = node[0]
        if typ in ("rectangle", "polyline", "circle", "arc", "pin", "property", "text"):
            acc.append(node)
        if typ == "symbol":
            for child in node[1:]:
                walk(child)
        if typ == "kicad_symbol_lib":
            for child in node[1:]:
                walk(child)

    walk(ast)

    extends = None
    for op in acc:
        if op[0] == "extends":
            extends = op[1]
            break

    if extends:
        try:
            parent_id = f"{category}:{extends}"
            parent_sexpr = fetch_sexpr(parent_id)
            parent_ops = _parse_sexpr_to_ops(parent_sexpr, category)
            parent_ops.extend(acc)
            return parent_ops
        except Exception:
            pass

    return acc


def _extract_pins_from_ops(ops: list, ref_des: str) -> dict:
    GRID_SIZE = 1.27
    pin_matrix = {}

    for op in ops:
        if op[0] != "pin":
            continue

        at = _get_attr(op, "at")
        len_node = _get_attr(op, "length")
        num_node = _get_attr(op, "number")
        if not at or not len_node or not num_node:
            continue

        try:
            px = float(at[1])
            py = float(at[2])
            ang_deg = float(at[3]) if len(at) > 3 else 0
            length = float(len_node[1])
        except (ValueError, IndexError):
            continue

        # Handle all rotation angles properly
        ang_rad = ang_deg * 3.14159 / 180.0
        cos_a = round(1.0 if ang_deg == 0 else (-1.0 if ang_deg == 180 else 0.0), 2)
        sin_a = round(1.0 if ang_deg == 90 else (-1.0 if ang_deg == 270 else 0.0), 2)
        
        # If not cardinal direction, use actual trig
        if abs(cos_a) < 0.1 and abs(sin_a) < 0.1:
            import math
            cos_a = math.cos(ang_rad)
            sin_a = math.sin(ang_rad)
        
        ex = px + cos_a * length
        ey = py + sin_a * length

        name_node = _get_attr(op, "name")
        pin_name = name_node[1] if name_node else ""
        pin_num = num_node[1].replace('"', '').strip()
        
        # Skip invalid pin numbers
        if not pin_num:
            continue

        key = f"{ref_des}:{pin_num}"
        
        # Avoid duplicate pin numbers (can happen with inherited symbols)
        if key in pin_matrix:
            continue
        
        pin_matrix[key] = {
            "x": round(ex / GRID_SIZE) * GRID_SIZE,
            "y": round(ey / GRID_SIZE) * GRID_SIZE,
            "name": pin_name.strip(),
            "ref_des": ref_des,
            "pin_num": pin_num,
        }

    return pin_matrix


def _get_attr(node, name):
    if not isinstance(node, list):
        return None
    for child in node[1:]:
        if isinstance(child, list) and child[0] == name:
            return child
    return None


def build_graph() -> StateGraph:
    builder = StateGraph(AgentState)

    builder.add_node("analyze", analyze_node)
    builder.add_node("research", research_node)
    builder.add_node("select", select_node)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("netlist", netlist_node)
    builder.add_node("layout_route", layout_route_node)

    builder.set_entry_point("analyze")
    builder.add_edge("analyze", "research")
    builder.add_edge("research", "select")
    builder.add_edge("select", "dispatch")
    builder.add_edge("dispatch", "netlist")
    builder.add_edge("netlist", "layout_route")

    return builder.compile()


agent_graph = build_graph()
