from agent.tools import search_components
from agent.utils import _emit, emit_assistant_message, emit_tool_event, _check_stage_contract, _stage_result, _extract_part_numbers, _sanitize_data


def research_node(state, config):
    contract = _check_stage_contract("research", state, ["analysis"])
    if contract:
        _emit(config, "agent:log", {"message": contract})
        return _stage_result(state, "research", {"research_results": []})
    analysis = state.get("analysis", [])
    if not analysis:
        _emit(config, "agent:log", {"message": "No subsystems to research."})
        return {"research_results": []}
    emit_assistant_message(config, "Searching for components across all subsystems...")
    emit_tool_event(config, "Component Research", "running", "Searching for components...")
    all_results = []
    user_parts = _extract_part_numbers(state.get("prompt", ""))
    if user_parts:
        _emit(config, "agent:thinking", {"message": f"Searching user-specified parts: {', '.join(user_parts)}..."})
        results, seen = [], set()
        for part in user_parts:
            try:
                for r in search_components(part, k=3):
                    if r["id_str"] not in seen:
                        seen.add(r["id_str"])
                        if r.get("text"):
                            r["text"] = _sanitize_data(r["text"], label=f"search:{r['id_str']}")
                        results.append(r)
            except Exception as e:
                print(f"Search failed for user part '{part}': {e}")
        if results:
            all_results.append({
                "subsystem": f"User-specified parts ({', '.join(user_parts)})",
                "function": "Parts explicitly requested by the user — MUST be selected when matching",
                "bus": "any",
                "results": results[:8],
            })
            _emit(config, "agent:log", {
                "message": f"  User-specified parts: found {len(results)} candidates"
            })

    # Phase 1: KiCad RAG search (existing)
    for sub in analysis:
        name = sub.get("subsystem", sub if isinstance(sub, str) else "unknown")
        examples = sub.get("example_components", [])
        if isinstance(examples, str):
            examples = [examples]
        queries = (examples[:2] if isinstance(examples, list) else []) + [name]
        _emit(config, "agent:thinking", {"message": f"Searching components for {name}..."})
        emit_tool_event(config, f"Research: {name}", "running", f"Searching {name}...")
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
                if r.get("text"):
                    r["text"] = _sanitize_data(r["text"], label=f"search:{r['id_str']}")
                deduped.append(r)
        all_results.append({
            "subsystem": name,
            "function": sub.get("function", ""),
            "bus": sub.get("bus", "any"),
            "results": deduped[:4],
        })
        emit_tool_event(config, f"Research: {name}", "completed", f"Found {len(deduped)} {name} candidates")
        _emit(config, "agent:log", {
            "message": f"  {name}: found {len(deduped)} candidates"
        })

    # Phase 1: Web research via DeepSearch (enhanced with live web data)
    _emit(config, "agent:thinking", {"message": "Searching web for component intelligence..."})
    emit_tool_event(config, "Web Component Research", "running", "Searching web for each subsystem...")
    web_results = []
    for sub in analysis:
        name = sub.get("subsystem", "?")
        function = sub.get("function", "")
        examples = sub.get("example_components", [])
        try:
            from agent.deep_search import deep_search
            summary = deep_search(
                f"Research electronic components for subsystem: {name}. "
                f"Function: {function}. "
                f"Example components: {examples}. "
                f"Return: recommended component types, popular part numbers, "
                f"typical specifications, and any design considerations.",
                config=config,
            )
            web_results.append({
                "subsystem": name,
                "summary": summary,
            })
            _emit(config, "agent:log", {"message": f"  Web research for '{name}': complete"})
        except Exception as e:
            _emit(config, "agent:log", {"message": f"  Web research for '{name}' failed: {e}"})
            web_results.append({
                "subsystem": name,
                "summary": f"(Web research failed: {e})",
            })

    total = sum(len(r.get("results", [])) for r in all_results)
    emit_tool_event(config, "Web Component Research", "completed", f"Researched {len(web_results)} subsystems on the web")
    emit_tool_event(config, "Component Research", "completed", f"Found {total} RAG candidates + {len(web_results)} web summaries across {len(analysis)} subsystems")
    emit_assistant_message(config, f"Found {total} component candidates across {len(analysis)} subsystems, plus web research data.")
    return {"research_results": all_results, "web_research_results": web_results}
