from agent.tools import fetch_sexpr, search_components
from agent.utils import _emit, _parse_sexpr_to_ops, _extract_pins_from_ops


def dispatch_node(state, config):
    pin_matrix = {}
    component_ops = {}
    for comp in state.get("selected_components", []):
        id_str = comp["id_str"]
        ref_des = comp["ref_des"]
        _emit(config, "agent:thinking", {"message": f"Loading {ref_des} ({id_str})..."})
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
    return {"pin_matrix": pin_matrix, "component_ops": component_ops}
