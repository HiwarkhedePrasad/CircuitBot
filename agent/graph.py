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
        pin_matrix.update(pins)
        component_ops[ref_des] = ops

        _emit(config, "agent:log", {
            "message": f"  Added {ref_des} ({len(pins)} pins)"
        })

    _emit(config, "agent:log", {"message": "All components loaded. Planning layout and routing..."})

    return {"pin_matrix": pin_matrix, "component_ops": component_ops}


def netlist_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Planning pin connections..."})

    comps = state.get("selected_components", [])
    pins = state.get("pin_matrix", {})
    if not comps or not pins:
        _emit(config, "agent:log", {"message": "No components or pins to route."})
        return {"netlist": []}

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
        netlist = json.loads(text) if text else []
    except json.JSONDecodeError:
        print(f"Failed to parse netlist JSON: {text[:200]}")
        netlist = []

    if not netlist:
        netlist = _generate_netlist_fallback(pins)

    _emit(config, "agent:log", {"message": f"Generated {len(netlist)} connections"})
    return {"netlist": netlist}


def layout_route_node(state: AgentState, config) -> dict:
    _emit(config, "agent:thinking", {"message": "Computing layout and routing wires..."})

    comp_ops = state.get("component_ops", {})
    comps = state.get("selected_components", [])
    pin_matrix = state.get("pin_matrix", {})
    netlist = state.get("netlist", [])

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

    _emit(config, "agent:layout_ready", {
        "placements": placements,
        "traces": traces,
    })

    _emit(config, "agent:done", {
        "message": f"Design complete: {len(engine.components)} components, {len(traces)} wires routed"
    })

    return {
        "component_placements": placements,
        "wire_paths": traces,
    }


def _generate_netlist_fallback(pin_matrix: dict) -> list:
    by_name = {}
    for key, pin in pin_matrix.items():
        name = pin.get("name", "")
        if not name:
            continue
        by_name.setdefault(name, []).append(key)
    netlist = []
    used = set()
    for name, keys in by_name.items():
        if len(keys) < 2:
            continue
        for i in range(1, len(keys)):
            pair = (keys[0], keys[i])
            if pair not in used:
                netlist.append({"source": keys[0], "target": keys[i]})
                used.add(pair)
    return netlist


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

        px = float(at[1])
        py = float(at[2])
        ang_deg = float(at[3]) if len(at) > 3 else 0
        length = float(len_node[1])

        cos_a = 1.0 if ang_deg == 0 else (-1.0 if ang_deg == 180 else 0.0)
        sin_a = 1.0 if ang_deg == 90 else (-1.0 if ang_deg == 270 else 0.0)
        ex = px + cos_a * length
        ey = py + sin_a * length

        name_node = _get_attr(op, "name")
        pin_name = name_node[1] if name_node else ""
        pin_num = num_node[1].replace('"', '')

        key = f"{ref_des}:{pin_num}"
        pin_matrix[key] = {
            "x": round(ex / GRID_SIZE) * GRID_SIZE,
            "y": round(ey / GRID_SIZE) * GRID_SIZE,
            "name": pin_name,
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
