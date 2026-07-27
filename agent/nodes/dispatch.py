from agent.tools import fetch_sexpr, search_components
from agent.utils import _emit, _parse_sexpr_to_ops, _extract_pins_from_ops, emit_thought, emit_tool_call, emit_tool_end, emit_step
from agent.feature_flags import is_enabled
from uuid import uuid4


def dispatch_node(state, config):
    dispatch_id = uuid4().hex[:8]
    emit_tool_call(config, dispatch_id, "Symbol Dispatch", "running")
    emit_thought(config, "Loading symbols for all selected components...")
    pin_matrix = {}
    component_ops = {}
    skipped_refs = []
    for comp in state.get("selected_components", []):
        id_str = comp.get("id_str", "")
        ref_des = comp.get("ref_des", "")
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
                            # The selected component list is frozen before
                            # dispatch. A description-based search result may
                            # have a different pinout, so it cannot silently
                            # replace the selected symbol at this stage.
                            if r["id_str"] != id_str:
                                ops = []
                                continue
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
    }
    if skipped_refs:
        result["error"] = (
            "Symbol dispatch failed for required component(s): "
            + ", ".join(f"{s['ref_des']} ({s['id_str']})" for s in skipped_refs)
        )

    # ── M1a: Build SynthesisGraph early (before netlist) ──────────────────
    if is_enabled("SYNTHESIS_GRAPH_EARLY") and state.get("selected_components"):
        try:
            from agent.synthesis.graph import SynthesisGraph
            from agent.synthesis.classifier import classify_all

            graph = SynthesisGraph()
            for c in state["selected_components"]:
                if c["ref_des"] in component_ops:
                    graph.add_component({
                        "ref_des": c["ref_des"],
                        "id_str": c["id_str"],
                        "library": c["id_str"].split(":")[0] if ":" in c["id_str"] else "",
                        "category": c.get("category", ""),
                        "description": c.get("description", ""),
                        "footprint": c.get("footprint", ""),
                    })
            for pin_key, pin_data in pin_matrix.items():
                ref = pin_key.split(":")[0]
                graph.add_pin(ref, pin_key, pin_data)
            classify_all(graph)
            result["synthesis_graph"] = graph
            emit_thought(config, f"Synthesis graph built: {len(graph.components)} components, "
                         f"{sum(len(c.pins) for c in graph.components.values())} pins classified")
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  Warning: SynthesisGraph build failed: {e}"})
            result["synthesis_graph_error"] = str(e)

    # ── M1a: Live knowledge extraction ────────────────────────────────────
    if is_enabled("KNOWLEDGE_EXTRACTION_LIVE") and state.get("selected_components"):
        try:
            from agent.knowledge_extractor import extract_knowledge
            from agent.component_knowledge import lookup_device

            knowledge_db = {}
            for c in state["selected_components"]:
                if c["ref_des"] not in component_ops:
                    continue
                comp_pins = {k: v for k, v in pin_matrix.items()
                             if k.startswith(c["ref_des"] + ":")}
                knowledge = extract_knowledge(c, comp_pins)
                device_info = lookup_device(c["id_str"], c.get("description", ""))
                if device_info:
                    knowledge["device_info"] = device_info
                knowledge_db[c["id_str"]] = knowledge
            result["knowledge_db"] = knowledge_db
            emit_thought(config, f"Knowledge extracted for {len(knowledge_db)} components")
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  Warning: Knowledge extraction failed: {e}"})
            result["knowledge_db_error"] = str(e)

    if skipped_refs:
        result["_skipped_components"] = skipped_refs
    return result
