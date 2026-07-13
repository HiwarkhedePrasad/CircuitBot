from agent.tools import fetch_sexpr, search_components
from agent.utils import _emit, _parse_sexpr_to_ops, _extract_pins_from_ops, emit_thought, emit_tool_call, emit_tool_end, emit_step
from uuid import uuid4


def dispatch_node(state, config):
    dispatch_id = uuid4().hex[:8]
    emit_tool_call(config, dispatch_id, "Symbol Dispatch", "running")
    emit_thought(config, "Loading symbols for all selected components...")
    pin_matrix = {}
    component_ops = {}
    skipped_refs = []
    for comp in state.get("selected_components", []):
        id_str = comp["id_str"]
        ref_des = comp["ref_des"]
        emit_step(config, dispatch_id, f"Loading {ref_des}...", "running")
        ops = []
        try:
            sexpr = fetch_sexpr(id_str)
            ops = _parse_sexpr_to_ops(sexpr, id_str.split(":")[0])
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
                            comp["id_str"] = id_str
                            break
                    except Exception:
                        continue
            except Exception:
                pass
        if not ops:
            skipped_refs.append({"ref_des": ref_des, "id_str": id_str})
            fp = comp.get("footprint", "") or "no footprint"
            _emit(config, "agent:log", {
                "message": f"  \u26a0 SKIPPED {ref_des} ({id_str}): no symbol found — "
                           f"footprint={fp}. This component MISSING from PCB."
            })
            continue
        _emit(config, "agent:component", {
            "id_str": id_str,
            "category": comp["category"],
            "ref_des": ref_des,
            "description": comp.get("description", ""),
            "footprint": comp.get("footprint", ""),
            "pads": comp.get("pads", []),
            "ops": ops,
        })
        pins = _extract_pins_from_ops(ops, ref_des)
        pin_names = [f"{p['pin_num']}:{p['name']}" for p in pins.values()]
        _emit(config, "agent:log", {
            "message": f"  Added {ref_des} ({len(pins)} pins): {', '.join(pin_names[:5])}" +
                      (f"... +{len(pins)-5} more" if len(pins) > 5 else "")
        })
        pin_matrix.update(pins)
        component_ops[ref_des] = ops
    _emit(config, "agent:log", {"message": "All components loaded. Planning layout and routing..."})
    if skipped_refs:
        skipped_msg = ", ".join(f"{s['ref_des']} ({s['id_str']})" for s in skipped_refs)
        _emit(config, "agent:log", {
            "message": f"  \u26a0 WARNING: {len(skipped_refs)} component(s) could not be loaded "
                       f"and will be ABSENT from the design: {skipped_msg}"
        })

    # Netlist freeze check: components removed by validator after last validation
    # pass (e.g. redundant crystal + load caps) will be MISSING from the layout.
    final_count = len(state.get("selected_components", []))
    validated_count = state.get("_last_validated_component_count", 0)
    if validated_count and final_count != validated_count:
        diff = final_count - validated_count
        _emit(config, "agent:log", {
            "message": f"  \u26a0 NETLIST FREEZE: {validated_count} components validated → "
                       f"{final_count} dispatched ({diff:+d}) — "
                       f"{'extra' if diff > 0 else 'missing'} component(s) in layout"
        })

    loaded = len(component_ops)
    skipped = len(skipped_refs)
    emit_step(config, dispatch_id, f"Loaded {loaded} symbols{' (' + str(skipped) + ' skipped)' if skipped else ''}", "completed")
    emit_tool_end(config, dispatch_id, f"Dispatched {loaded} component symbols" + (f", {skipped} skipped" if skipped else ""),
                   status="completed" if not skipped else "failed")
    result = {
        "pin_matrix": pin_matrix,
        "component_ops": component_ops,
        "retry_count": 0,
        "validation_errors": [],
    }
    if skipped_refs:
        result["_skipped_components"] = skipped_refs
    return result
